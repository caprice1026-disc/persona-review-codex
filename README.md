# persona-review

好きなキャラクターの口調と表情で、根拠に基づくコードレビューを受けるためのCodexプラグインのたたき台です。ローカルのファイル・プロジェクト、接続済みGitHubコネクタで読めるURLを対象にします。技術評価を確定してから口調を変える手順を採り、辛口の設定でも問題や重大度を増やさない方針です。

出力は、キャラクター画像、ひと言、技術総評、良い点、根拠付きの問題点と改善案、対象範囲・未確認箇所の順です。画像を登録していない状態でもレビューできます。初期状態に画像は含まれていません。

## 導入

Python 3.10以降と、スキルを利用できるCodex環境が必要です。ローカルの回帰テストはWindowsのPython 3.10でも検証しています。Pythonライブラリの追加、HTTPサーバー、MCPサーバー、APIキーは不要です。

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
|保存画像のみ (`cached_only`)|有効な保存画像だけを利用。欠損・破損・未登録でも生成せず、理由とレビュー本文を表示|
|ハイブリッド (`hybrid`)|同じキャラクター・参照画像・感情の有効画像を再利用。不足時は生成して保存|
|最初に全部生成 (`pre_generate`)|`happy / neutral / concerned / stern` の4表情を初期生成。有効な既存画像は再利用し、不足分だけ生成・保存|

生成を行う3モードでは、生成画像をプラグイン外のユーザーデータへ永続保存します。古い画像を上書き・削除せず、感情キー、生成元、キャラクター識別値、参照画像と生成画像のSHA-256を記録します。キャラクターID・名前・外見・参照画像の内容が変わると別のキャッシュになり、口調だけの変更では同じ画像を使えます。

旧設定は `A → always`、`B → pre_generate`、`hybrid → hybrid` と読み込みます。旧Bは「画像がなければ何も生成しない」動作から、不足する4表情を生成する動作へ変わります。生成を禁止する指定は `cached_only` です。設定の読み込みや移行コマンドだけでは生成しません。旧登録画像は初回の画像計画・更新時に履歴へ取り込み、元ファイルを保持します。更新後に旧版から設定を書き戻す運用は対象外です。

生成に使うのはCodex標準の `image_gen` だけです。利用できない場合、または参照画像がない場合は、理由を添えてレビュー本文を届けます。ソースコード、秘密情報、問題の詳細は画像生成に渡しません。

キャラクターは外見と画像、人格は話し方として別々に登録できます。同じ人格を複数キャラクターに使い、同じキャラクターの人格だけを変更できます。人格の切り替えだけでは画像を生成しません。

> review-configで「率直」という人格を登録して、ミナとガイドの両方で使えるようにして。今回はミナの外見のまま「率直」に切り替えて。

> 今回は保存画像だけで、このファイルをセキュリティと性能の観点でレビューして。

レビュー範囲 `scope` は `auto / diff / full / file`、重点 `focus` は `correctness / security / performance / architecture / maintainability / testing` の配列です。両方を独立して指定でき、重点を指定しても発見した重大な問題を隠しません。`focus=[]` は重点を限定しない指定です。旧 `review_mode` は `scope` の別名として受け付けます。

## ヘルパーを試す

以下はリポジトリのルートから実行します。`register` と `set` はユーザーデータを更新するコマンドです。サンプルは架空のキャラクターと空の差分を使い、個人コードや参照画像を含みません。

```powershell
python scripts/persona_review.py show
python scripts/persona_review.py register-persona calm examples/persona.json
python scripts/persona_review.py register mina examples/character-v2.json
python scripts/persona_review.py set character_id mina
python scripts/persona_review.py set image_mode hybrid
python scripts/persona_review.py set scope auto
python scripts/persona_review.py set focus security,performance
python scripts/persona_review.py resolve
python scripts/persona_review.py validate-review examples/review.json
python scripts/persona_review.py plan examples/review.json
python scripts/persona_review.py plan-set --character mina
```

画像を登録する際は `python scripts/persona_review.py import-image mina reference <画像パス>` を使います。`reference` を `happy`、`neutral`、`concerned`、`stern` にすると表情画像を登録できます。差し替えは同じコマンドで行います。設定を試すだけの場合は `--data-dir <一時ディレクトリ>` をサブコマンドの前に指定してください。

生成成功後は、生成前に保存した1件の計画JSONと実画像を `save-generated <計画JSON> <画像パス>` に渡します。同じ計画・画像の再保存は重複を作りません。生成失敗では成功記録を作らず、初期セットの一部が失敗しても成功分を保持します。次回の `plan-set` は不足分だけを生成対象にします。破損画像は `invalidate-image <記録ID>` で再利用対象から外せます。詳しい手順は [画像ワークフロー](docs/image-workflow.md) を参照してください。

設定の優先順位は **明示指定 > プロジェクト設定 > グローバル設定 > 同梱既定値** です。プロジェクト設定は既定で無効になっています。許可する場合は `set allow_project_overrides true` を使います。詳細は [設計](docs/design.md) を参照してください。

## 検証と制約

v0.1.0、v0.2.0、および今回のv0.3.0は、作成担当とは独立したGPT-6 Astraのレビューと指摘修正後の再確認を完了しています。v0.3.0では品質回帰テスト・CI、4画像モード、scope/focusとcharacter/personaの分離、v1設定移行を確認しました。共有された旧人格を片方の旧形式registerで変更しても、他方の口調を変えない修正を含みます。Windows Python 3.10.9のローカルテストは82件中81成功・1スキップ（symlink作成権限）です。Windows/LinuxのCI結果は[Actions](https://github.com/caprice1026-disc/persona-review-codex/actions)で対象commitごとに確認してください。

2026年10月18日にWindowsのCodex CLI 0.163.0-alpha.10.2 / gpt-6.1-solで、v0.3.0を専用marketplaceへ実インストールし、空の専用データ領域への自然言語設定からレビュー・画像表示まで確認しました。インストール済み35ファイルを修正版ソースと照合し、通常のユーザー設定は保持しました。人格切替、scope=file/diffとfocusの個別設定、共有人格の移行・編集・再登録（38項目）を確認しています。

| 実画像の受け入れ | 実測結果 |
|---|---|
| cached_only | 画像なしでは生成0回で理由と本文を返し、画像ありでは保存画像を表示 |
| hybrid | neutralを1回生成・保存・表示し、2回目は同じ記録を生成0回で再利用 |
| 人格だけの切替 | calm/directの両方で同じ画像記録・ファイル・ハッシュを再利用、生成0回 |
| pre_generate | 既存neutralを保持し、不足3表情だけ生成・保存。4表情complete後の再試行は生成0回 |
| always | 保存画像があっても新しい計画で2回生成し、異なる記録・画像として保存・表示。旧画像も保持 |

再現時は本番と分けた空の `--data-dir` と架空の参照画像を使い、登録→cached_only欠落→hybrid初回/再利用→人格切替→pre_generate/再試行→cached_only既存→alwaysを2回、の順で確認します。[画像ワークフロー](docs/image-workflow.md)に従って各plan・保存応答・画像を保持し、実際に表示してください。ヘルパーだけでは生成・表示の検証になりません。今回の表情生成6枚は、Codexが返した画像と永続保存先のハッシュを照合しました。

接続済みGitHubコネクタで実PRの差分を取得し、base/headを固定した14ファイルの入力を実Codexで中立JSON→人格本文→画像表示へ通しました。人格版のtechnicalは中立JSONと一致し、未確認事項とpartial評価を保持しました。CLI自身でのGitHub再取得、対象コードやテストの実行は行っていません。この実PRには正解ラベルがないため、指摘0件を精度の証明には使いません。v0.2.0では隔離コピーのデコード故障後の再試行も確認済みです。生成サービス自体の障害、デスクトップGUI内の全操作、画像品質の定量評価は未検証です。

```powershell
python -m unittest discover -s tests -v
python scripts/persona_review.py validate-review examples/review.json
python tests/review_quality.py check tests/fixtures/review_quality/reference-results.json
```

4つの合成コード例を使い、既知の不具合、正常コード、コード内の偽命令、未取得の関連実装を扱うレビューの契約テストを追加しています。2つのペルソナで中立JSONの指摘・重大度・根拠・未確認事項を保持することを検査します。CIの期待結果は手作りで、実LLMの出力ではありません。別途 gpt-6.1-sol / xhigh で中立4例・2人格×4例を実行し、scopeの出力契約を揃えた再評価で全件通過しました。最初の4件のscope不一致は失敗として保持しています。この少数例の結果を一般的な精度保証には使いません。Windows/Linux × Python 3.10/3.13のCIはAPIキーなしでこの決定論的検証を行います。[実モデル評価の別手順と保証範囲](docs/review-quality.md)を参照してください。

テストでは設定の優先順位、不正設定の拒否、保存失敗時の維持、ロック、画像モードの分岐、表情の優先順位を確認します。外部のレビュー対象コードは実行しません。技術レビューと口調の変換はCodexのスキル手順であり、ヘルパー単独でLLMや画像ツールを起動することはありません。

旧設定は読み取りでは書き換えず、次の保存または `migrate` でv2へ移行します。元の設定を同じユーザーデータ内の `config.v1-backup-<SHA256>.json` に保存し、画像と履歴を維持します。[移行と復旧](docs/configuration-v2.md)を参照してください。

中間JSONの検証は、指摘の技術的正しさや画像の品質を保証しません。画像インポートは形式ヘッダーとサイズの確認までなので、スキル側で画像を開いて確認します。設定と画像の同時更新が中断された場合、未参照の画像ファイルが残ることがあります。古い画像も自動削除しません。

参照画像、個人コード、秘密情報、実際のレビュー結果はコミットしないでください。ユーザーデータはプラグイン外へ保存し、`.persona-review.json` も既定の `.gitignore` に含めています。他のリポジトリに設定を作る場合は、そのリポジトリでも追跡しないようにしてください。
