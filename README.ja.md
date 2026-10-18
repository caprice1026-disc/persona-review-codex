# Persona Review

[English](README.md)

根拠に基づくコードレビューを、好きなキャラクターの口調と表情で届けるCodexプラグインです。技術評価を先に確定し、指摘・重大度・根拠・未確認事項を保って口調を変えます。辛口でも問題を捏造せず、正常なコードを無理に批判しません。

**0.4.0-rc.1は未リリースの配布準備版です。** 現在はPython 3.10以上が必要です。同梱shell/PowerShellはPythonを起動するラッパーです。Pythonがない場合のBash代替は検討中で、まだ実装していません。

## 必要な環境

- ログイン済みで、ローカルプラグイン・Skills・ローカル実行に対応するCodex。`requires_local_executor` を指定しています。対応機能はアカウントとバージョンに依存します。
- 設定保存・検証・画像管理にはPython 3.10以上。標準ライブラリだけを使い、pip追加パッケージ、Node.js、jq、HTTP/MCPサーバー、ヘルパー用の別APIキーは不要です。
- 新しい表情の生成にはCodex標準の `image_gen` と登録済み参照画像。利用できない場合は理由とレビュー本文を返します。画像は同梱していません。
- GitHub URLには接続済みGitHubリーダー。ローカルファイルも対象にできます。

|OS|ヘルパー|任意のランチャー|
|---|---|---|
|Windows|Python 3.10以上|PowerShell 5.1以上。`py -3`、`python3`、`python` を確認|
|macOS|Python 3.10以上|POSIX sh。`python3`、`python` を確認|
|Linux|Python 3.10以上|POSIX sh。`python3`、`python` を確認|

JavaScript開発環境だけではPythonがあるとは限りません。ランチャーは自動インストールや実行ポリシー変更をしません。[実行環境の案内](docs/runtime.md)を参照してください。

## 導入

レビュー済みの `persona-review-marketplace-<version>.zip` を管理者から受け取ります。この準備版の公開Releaseはありません。privateソースの取得には権限が必要です。管理者は [配布手順](docs/distribution.md) からZIPを作れます。

1. ZIPを新しいフォルダへ展開します。catalogと正しい階層が含まれるので、JSONの手編集は不要です。
2. 普段使うログイン済みCodexで `codex --version`、`codex plugin --help`、`codex login status` を確認します。コマンドがなければ [公式CLI導入手順](https://developers.openai.com/codex/cli/) またはインストール済みCLIの案内に従います。個人固有のアプリ内部パスを固定したり、認証を複製したりしません。
3. 展開された `persona-review-marketplace` 内で実行します。

   ```text
   codex plugin marketplace add .
   codex plugin add persona-review@persona-review-local
   codex plugin list --json
   ```

4. 新しいCodexセッションを開き、Skill一覧に `persona-review:review` と `persona-review:review-config` があることを確認して、「persona-review:review-configで現在の設定を見せて」と依頼します。通常利用ではSkillの絶対パスを手渡しません。認識されなければ [導入確認](docs/runtime.md#plugin-discovery) を行います。

Windowsでの展開例:

```powershell
Expand-Archive -LiteralPath .\persona-review-marketplace-0.4.0-rc.1.zip -DestinationPath .\persona-review-preview
Set-Location .\persona-review-preview\persona-review-marketplace
```

macOS/Linux（既存のunzipまたはGUI展開ツール）:

```sh
unzip persona-review-marketplace-0.4.0-rc.1.zip -d persona-review-preview
cd persona-review-preview/persona-review-marketplace
```

CLIコマンドは0.163.0-alpha.10.2で確認した形式です。他版では手元のhelpを確認してください。[公式パッケージ仕様](https://developers.openai.com/plugins/build/plugins) のローカルmarketplaceと公開申請は別です。単体プラグインZIPをmarketplaceとして登録しないでください。

## 自然言語で使う

> persona-review:review-configで「灯台守ミナ」を登録して。口調は落ち着いて率直に。添付画像を参照画像にして、画像モードはhybrid、既定キャラクターはミナにして。

> ミナにこのプロジェクトの変更点を、セキュリティと正しさを中心にレビューしてもらいたい。

> ミナの外見のまま、今回は登録済みの「率直」な人格へ切り替えて。

キャラクターの外見と人格の話し方は独立しています。範囲は `auto / diff / full / file`、重点は correctness/security/performance/architecture/maintainability/testing。重点外でも重大な問題は隠さず、未読コードや実行していないテストは未確認のまま残します。

|画像モード|動作|
|---|---|
|都度生成 `always`|毎回新しい表情を生成・保存|
|保存画像のみ `cached_only`|有効な保存画像だけを利用。不足・破損時も生成しない|
|ハイブリッド `hybrid`|同じキャラクター・参照画像・感情を再利用。不足時に生成・保存|
|最初に全部生成 `pre_generate`|happy/neutral/concerned/sternの4表情を準備。有効画像は再利用し、不足分だけ再試行|

画像・履歴はプラグイン外へ永続保存します。外見・参照画像の内容変更は別キャッシュになりますが、人格変更だけなら再利用します。古い画像は削除しません。生成機能がなくても `cached_only` は有効な保存画像を使えます。使える画像がなければ理由と本文を届けます。

## ヘルパーを試す

プラグインのフォルダから実行します。macOS/Linuxでは先頭の `& .\scripts\persona-review.ps1` を `sh scripts/persona-review.sh` に置き換えます。確認済みPythonから `scripts/persona_review.py` を直接呼ぶこともできます。

```powershell
& .\scripts\persona-review.ps1 --data-dir "../demo data" show
& .\scripts\persona-review.ps1 --data-dir "../demo data" register-persona calm examples/persona.json
& .\scripts\persona-review.ps1 --data-dir "../demo data" register mina examples/character-v2.json
& .\scripts\persona-review.ps1 --data-dir "../demo data" set character_id mina
& .\scripts\persona-review.ps1 --data-dir "../demo data" resolve
& .\scripts\persona-review.ps1 validate-review examples/review.json
```

**`--data-dir` はサブコマンドより前です。** `--data-dir PATH resolve` が正しく、`resolve --data-dir PATH` は使えません。試用には新しい一時領域を指定してください。保存コマンドは指定領域へ書き込みます。

PowerShellでは `--focus 'correctness,security'` のようにカンマ区切り値を引用してください。`show` / `resolve` は未作成の保存先を作りません。画像の `plan` はcached_onlyでも画像カタログを作成する場合があります。

既定保存先はWindowsで `%LOCALAPPDATA%/persona-review`、macOS/Linuxでは `$XDG_DATA_HOME/persona-review` または `~/.local/share/persona-review`。`PERSONA_REVIEW_HOME` / `--data-dir` でも指定できます。プロジェクト上書きは既定で無効です。[設定・移行](docs/configuration-v2.md) と [画像ワークフロー](docs/image-workflow.md) を参照してください。

v1設定は読むだけなら変えず、保存または `migrate` 時に元ファイルのバックアップを作ってv2へ移行します。旧Aはalways、旧Bはpre_generateです。旧Bは不足画像を生成する動作へ変わるため、生成禁止ならcached_onlyを選びます。移行後に旧ヘルパーで書き戻す運用は対象外です。

## 検証と制約

CIはAPIキーなしの決定論的テストです。Windows/macOS/Linux × Python 3.10/3.13の設定を追加していますが、実行結果は対象commitで確認します。既存v0.3.0はWindows/Linuxの4ジョブで各82件成功しています。

独立Windows検証では空の設定2領域へのv0.3.0導入・Skill認識と、新しいデータ領域での設定・レビュー・画像生成保存を確認しました。認証付き実行は導入確認と分離し、Skillパスを補助指定しています。完全無補助導入、Desktop最終画面の画像表示、非Windows実Codexは未確認です。[検証範囲](docs/validation.md) を参照してください。

ソースcheckoutからの検証（runtime ZIPにはtests/build toolsを含みません）:

```text
python -m unittest discover -s tests -v
python tests/review_quality.py check tests/fixtures/review_quality/reference-results.json
python tools/build_dist.py --output dist
python tools/build_dist.py --output dist --check
```

品質fixtureは手作りの契約例で、実LLM出力ではありません。JSON検証や少数の実モデル評価は一般的な精度を保証しません。画像確認はヘッダー・サイズ・ハッシュまでで、内容はSkill側で目視します。ヘルパーは対象コードを実行せず、LLMや画像APIも呼びません。[品質評価](docs/review-quality.md) と [設計](docs/design.md) を参照してください。

## ライセンス

[Apache-2.0](LICENSE)。個人画像、対象コード、設定、認証、実レビュー結果は同梱せず、コミットしないでください。
