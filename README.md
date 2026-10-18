# persona-review

好きなキャラクターの口調と表情で、根拠に基づくコードレビューを受けるためのCodexプラグインのたたき台です。ローカルのファイル・プロジェクト、接続済みGitHubコネクタで読めるURLを対象にします。技術評価を確定してから口調を変える手順を採り、辛口の設定でも問題や重大度を増やさない方針です。

出力は、キャラクター画像、ひと言、技術総評、良い点、根拠付きの問題点と改善案、対象範囲・未確認箇所の順です。画像を登録していない状態でもレビューできます。初期状態に画像は含まれていません。

## 導入

Python 3.10以降と、スキルを利用できるCodex環境が必要です。開発時はWindowsのPython 3.13で検証しています。Pythonライブラリの追加、HTTPサーバー、MCPサーバー、APIキーは不要です。

このリポジトリはルートの `plugin.json` と `skills/` を使うportable形式です。Codexのプラグイン導入手順に従ってこのディレクトリをインストールしてください。ローカルmarketplaceへ組み込む場合は、marketplaceのルートから見て `plugins/persona-review` にこのディレクトリを配置し、`.agents/plugins/marketplace.json` のプラグイン項目で次を指定できます。

```json
{
  "name": "persona-review-local",
  "plugins": [{
    "name": "persona-review",
    "source": {"source": "local", "path": "./plugins/persona-review"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Developer Tools"
  }]
}
```

検証したCodex CLIでは、`codex plugin marketplace add <marketplaceのルート>`、`codex plugin add persona-review@persona-review-local` の順にインストールできます。導入後は新しいCodexセッションでスキルを使います。CLIの利用可否はインストール先とバージョンに依存するため、手元の `codex plugin --help` も確認してください。

形式の根拠は [OpenAI公式のプラグイン仕様](https://developers.openai.com/plugins/build/plugins) と [スキル仕様](https://developers.openai.com/plugins/build/skills) です。2026年10月18日に確認した形式を使用しています。Hooksはレビュー依頼の範囲外で処理を開始しないよう、同梱していません。

## 自然言語で使う

インストール後、次のように依頼できます。

> review-configで「灯台守ミナ」を登録して。口調は落ち着いて率直に。添付画像を参照画像にして、画像モードはhybrid、既定キャラクターはミナにして。

> ミナにこのプロジェクトの変更点をレビューしてもらいたい。

> このGitHub PRをレビューして。今回は都度生成で、セキュリティを中心に見て。

> ミナの画像は最初に全部生成にして。保存済みの表情はそのまま使って。

> ミナの不足している表情だけ、もう一度生成して。

> このプロジェクトだけ画像モードをハイブリッドにして。プロジェクト設定による上書きも許可して。

`review` が対象の選択とレビューを担当し、`review-config` が登録・変更を担当します。キャラクターを差し替えても評価基準は同じです。GitHubからの読み取りに使えるコネクタがなければ、取得できなかったことを伝えます。

|画像モード|動作|
|---|---|
|都度生成 (`always`)|保存画像があっても毎回そのレビューの表情を生成し、新しい画像として保存|
|ハイブリッド (`hybrid`)|同じキャラクター・参照画像・感情の有効画像を再利用。不足時は生成して保存|
|最初に全部生成 (`pre_generate`)|`happy / neutral / concerned / stern` の4表情を初期生成。有効な既存画像は再利用し、不足分だけ生成・保存|

全モードで生成画像をプラグイン外のユーザーデータへ永続保存します。古い画像を上書き・削除せず、感情キー、生成元、キャラクター識別値、参照画像と生成画像のSHA-256を記録します。キャラクターID・名前・外見・参照画像の内容が変わると別のキャッシュになり、口調だけの変更では同じ画像を使えます。

旧設定は `A → always`、`B → pre_generate`、`hybrid → hybrid` と読み込みます。旧Bは「画像がなければ何も生成しない」動作から、不足する4表情を生成する動作へ変わります。設定の読み込みや移行コマンドだけでは生成しません。旧登録画像は初回の画像計画・更新時に履歴へ取り込み、元ファイルを保持します。更新後に旧版から設定を書き戻す運用は対象外です。

生成に使うのはCodex標準の `image_gen` だけです。利用できない場合、または参照画像がない場合は、理由を添えてレビュー本文を届けます。ソースコード、秘密情報、問題の詳細は画像生成に渡しません。

## ヘルパーを試す

以下はリポジトリのルートから実行します。`register` と `set` はユーザーデータを更新するコマンドです。サンプルは架空のキャラクターと空の差分を使い、個人コードや参照画像を含みません。

```powershell
python scripts/persona_review.py show
python scripts/persona_review.py register mina examples/character.json
python scripts/persona_review.py set character mina
python scripts/persona_review.py set image_mode hybrid
python scripts/persona_review.py resolve
python scripts/persona_review.py validate-review examples/review.json
python scripts/persona_review.py plan examples/review.json
python scripts/persona_review.py plan-set --character mina
```

画像を登録する際は `python scripts/persona_review.py import-image mina reference <画像パス>` を使います。`reference` を `happy`、`neutral`、`concerned`、`stern` にすると表情画像を登録できます。差し替えは同じコマンドで行います。設定を試すだけの場合は `--data-dir <一時ディレクトリ>` をサブコマンドの前に指定してください。

生成成功後は、生成前に保存した1件の計画JSONと実画像を `save-generated <計画JSON> <画像パス>` に渡します。同じ計画・画像の再保存は重複を作りません。生成失敗では成功記録を作らず、初期セットの一部が失敗しても成功分を保持します。次回の `plan-set` は不足分だけを生成対象にします。破損画像は `invalidate-image <記録ID>` で再利用対象から外せます。詳しい手順は [画像ワークフロー](docs/image-workflow.md) を参照してください。

設定の優先順位は **明示指定 > プロジェクト設定 > グローバル設定 > 同梱既定値** です。プロジェクト設定は既定で無効になっています。許可する場合は `set allow_project_overrides true` を使います。詳細は [設計](docs/design.md) を参照してください。

## 検証と制約

v0.1.0は作成担当とは独立したGPT-6 Astraのレビューと指摘修正後の再確認を完了しています。今回のv0.2.0変更は独立レビュー指摘の修正後、再確認待ちです。設定の保持、UTF-8バイト数上限、画像履歴、キャッシュ分離、保存失敗からの再試行など31件のテストが成功しています。

Windows上のCodex CLIへの実インストール、自然言語設定、合成コードのレビュー、標準 `image_gen` の生成・表示を確認しました。v0.2.0では実画像の永続保存、hybridでの同じ表情の再利用、alwaysでの追加保存、初期4表情の不足分だけの生成、隔離コピーのデコード故障後の再試行を確認しています。画像障害中も中立レビューと技術本文を保持しました。GitHubコネクタを使った入力取得から出力までのE2E、生成サービス自体の障害、デスクトップGUI内の全操作は未検証です。

```powershell
python -m unittest discover -s tests -v
python scripts/persona_review.py validate-review examples/review.json
```

テストでは設定の優先順位、不正設定の拒否、保存失敗時の維持、ロック、画像モードの分岐、表情の優先順位を確認します。外部のレビュー対象コードは実行しません。技術レビューと口調の変換はCodexのスキル手順であり、ヘルパー単独でLLMや画像ツールを起動することはありません。

中間JSONの検証は、指摘の技術的正しさや画像の品質を保証しません。画像インポートは形式ヘッダーとサイズの確認までなので、スキル側で画像を開いて確認します。設定と画像の同時更新が中断された場合、未参照の画像ファイルが残ることがあります。古い画像も自動削除しません。

参照画像、個人コード、秘密情報、実際のレビュー結果はコミットしないでください。ユーザーデータはプラグイン外へ保存し、`.persona-review.json` も既定の `.gitignore` に含めています。他のリポジトリに設定を作る場合は、そのリポジトリでも追跡しないようにしてください。
