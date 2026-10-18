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

marketplaceを追加するCLIは `codex plugin marketplace add <marketplaceのルート>` です。インストールとスキルの試用はデスクトップのプラグイン画面から行います。この開発環境ではCodex CLIがPATHにないため、実機インストールと画像生成の一連の動作は未検証です。ユーザー環境への自動登録は行いません。

形式の根拠は [OpenAI公式のプラグイン仕様](https://developers.openai.com/plugins/build/plugins) と [スキル仕様](https://developers.openai.com/plugins/build/skills) です。2026年10月18日に確認した形式を使用しています。Hooksはレビュー依頼の範囲外で処理を開始しないよう、同梱していません。

## 自然言語で使う

インストール後、次のように依頼できます。

> review-configで「灯台守ミナ」を登録して。口調は落ち着いて率直に。添付画像を参照画像にして、画像モードはhybrid、既定キャラクターはミナにして。

> ミナにこのプロジェクトの変更点をレビューしてもらいたい。

> このGitHub PRをレビューして。今回は画像モードBで、セキュリティを中心に見て。

> このプロジェクトだけ画像モードをBにして。プロジェクト設定による上書きも許可して。

`review` が対象の選択とレビューを担当し、`review-config` が登録・変更を担当します。キャラクターを差し替えても評価基準は同じです。GitHubからの読み取りに使えるコネクタがなければ、取得できなかったことを伝えます。

|画像モード|動作|
|---|---|
|A|登録した参照画像から、毎回そのレビューの表情を生成|
|B|登録済みの該当表情を選択。なければ画像なし|
|hybrid|登録済みの該当表情を優先し、なければ参照画像から生成|

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
```

画像を登録する際は `python scripts/persona_review.py import-image mina reference <画像パス>` を使います。`reference` を `happy`、`neutral`、`concerned`、`stern` にすると表情画像を登録できます。差し替えは同じコマンドで行います。設定を試すだけの場合は `--data-dir <一時ディレクトリ>` をサブコマンドの前に指定してください。

設定の優先順位は **明示指定 > プロジェクト設定 > グローバル設定 > 同梱既定値** です。プロジェクト設定は既定で無効になっています。許可する場合は `set allow_project_overrides true` を使います。詳細は [設計](docs/design.md) を参照してください。

## 検証と制約

2026年10月18日、作成担当とは独立したGPT-6 Astraのレビューを受け、指摘されたJSON保存サイズ上限の不整合を修正し、修正後の再確認を完了しました。既存設定の保持とUTF-8バイト数の境界を含む20件のテストが成功しています。

Codexへの実インストール、GitHubコネクタを使った入力取得から出力までのE2E、実際の `image_gen` による画像生成は未検証です。

```powershell
python -m unittest discover -s tests -v
python scripts/persona_review.py validate-review examples/review.json
```

テストでは設定の優先順位、不正設定の拒否、保存失敗時の維持、ロック、画像モードの分岐、表情の優先順位を確認します。外部のレビュー対象コードは実行しません。技術レビューと口調の変換はCodexのスキル手順であり、ヘルパー単独でLLMや画像ツールを起動することはありません。

中間JSONの検証は、指摘の技術的正しさや画像の品質を保証しません。画像インポートは形式ヘッダーとサイズの確認までなので、スキル側で画像を開いて確認します。設定と画像の同時更新が中断された場合、未参照の画像ファイルが残ることがあります。古い画像も自動削除しません。

参照画像、個人コード、秘密情報、実際のレビュー結果はコミットしないでください。ユーザーデータはプラグイン外へ保存し、`.persona-review.json` も既定の `.gitignore` に含めています。他のリポジトリに設定を作る場合は、そのリポジトリでも追跡しないようにしてください。
