# BigQuery Text-to-SQL 分析ツール

## 概要
このプロジェクトは、自然言語での質問をもとに LangChain と OpenAI の ChatGPT モデルで BigQuery 用の SQL を生成し、データを取得・分析してインサイトを抽出する自動化ツールです。取得した結果はファイルに保存され、任意で Slack チャンネルへ投稿できます。

## 主な機能
- LangChain の SQL チェーンを使った自然言語からの SQL 自動生成
- Google Cloud BigQuery へのクエリ実行と結果の要約
- pandas と matplotlib による統計量算出と簡易チャート作成
- インサイト、実行 SQL、CSV データの保存
- Slack への結果共有（オプション）

## 前提条件
- Python 3.10 以上
- BigQuery プロジェクトと対象データセット
- OpenAI API キー（`gpt-5-mini` などを利用可能なプラン）
- Slack ワークスペース（結果を投稿する場合）
- Google Cloud のサービスアカウント JSON キー

## セットアップ
1. リポジトリをクローンします。
   ```bash
   git clone <REPO_URL>
   cd bigquery-text-to-sql
   ```
2. 仮想環境を作成して有効化します。
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```
3. 依存関係をインストールします。
   ```bash
   pip install -r requirements.txt
   ```

## 環境変数（`.env`）設定
プロジェクトルートに `.env` を作成し、以下の変数を設定してください。

| 変数名 | 必須 | 説明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | ✅ | OpenAI API キー |
| `PROJECT_ID` | ✅ | BigQuery プロジェクト ID |
| `DEFAULT_DATASET` | ✅ | BigQuery データセット名 |
| `BQ_LOCATION` | ⭕️ | BigQuery のリージョン（例: `US`） |
| `GOOGLE_APPLICATION_CREDENTIALS` | ✅ | サービスアカウント JSON キーへの絶対パス |
| `OUTPUT_DIR` | ⭕️ | 結果保存先ディレクトリ（デフォルト: `./output`） |
| `SLACK_BOT_TOKEN` | ⭕️ | Slack Bot User OAuth Token（投稿する場合） |
| `SLACK_CHANNEL_ID` | ⭕️ | 投稿先チャンネル ID（`C123...` 形式） |

## 使い方
1. `.env` を設定し、仮想環境を有効化します。
2. スクリプトを実行します。
   ```bash
   python src/bigquery_data_insights_slack.py
   ```
3. プロンプトに従って分析したい質問を入力します。  
   例: `地域別のデータを集計して、傾向を分析して下さい。`
4. スクリプトが自動で BigQuery にアクセスしてデータを取得し、統計情報とインサイトを生成します。
5. 生成された結果は `OUTPUT_DIR/Output` 配下に保存され、Slack トークンとチャンネル ID を設定している場合は同時に Slack に投稿されます。

> **メモ:** Slack 連携が不要な場合は、`src/bigquery_data_insights_slack.py` の末尾にある `ask_and_analyze(..., post_to_slack=True)` を `False` に変更してください。

## 出力ファイル
デフォルトでは `./output/Output` 以下に次のファイルが生成されます。
- `insights.txt`：質問と抽出されたインサイト
- `queries.sql`：実行された SQL クエリ
- `data.csv`（または `data_*.csv`）：取得データ
- `chart.png`：数値・カテゴリ列の簡易可視化（生成可能な場合）

## Slack 連携について
- Slack App を作成し、必要な権限（`chat:write`, `files:write` など）を付与した Bot Token を用意してください。
- Bot を投稿先チャンネルに招待した上で、`SLACK_CHANNEL_ID` にチャンネル ID を設定します。
- 実行時に Slack 設定が不足している場合は警告が出て投稿処理はスキップされます。

## 開発メモ
- データセット名に `project.dataset` 形式を指定した場合は、自動でデータセット部分のみが利用されます。
- `DEFAULT_LIMIT` は 10,000 行に設定されており、`LIMIT` や集計が含まれないクエリには自動で付与されます。
- matplotlib のフォントは日本語表示が可能なものが見つかれば自動設定されます。
