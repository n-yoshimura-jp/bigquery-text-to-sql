from pathlib import Path
from datetime import datetime
import os
import json

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import font_manager
from dotenv import load_dotenv
from sqlalchemy import create_engine
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI
from langchain.chains import create_sql_query_chain

# ============================================================
# 設定の読み込み
# ============================================================
load_dotenv(override=True)


def configure_matplotlib_font() -> None:
    """日本語フォントの設定"""
    font_candidates = [
        "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic",
        "MS Gothic", "Meiryo", "Noto Sans CJK JP", "IPAexGothic"
    ]
    available_fonts = {f.name for f in font_manager.fontManager.ttflist}
    for font_name in font_candidates:
        if font_name in available_fonts:
            plt.rcParams["font.family"] = font_name
            break
    plt.rcParams["axes.unicode_minus"] = False


configure_matplotlib_font()

# 環境変数の読み込み
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PROJECT_ID = os.getenv("PROJECT_ID")
DATASET = os.getenv("DEFAULT_DATASET")
LOCATION = os.getenv("BQ_LOCATION")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./output"))
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL_ID")
CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

DEFAULT_LIMIT = 10000

# データセット名の修正
if DATASET and '.' in DATASET:
    DATASET = DATASET.split('.')[-1]

# 必須項目チェック
if not all([OPENAI_API_KEY, PROJECT_ID, DATASET, CREDENTIALS_PATH]):
    raise ValueError("必要な環境変数が設定されていません")

# BigQuery接続
connection_string = f"bigquery://{PROJECT_ID}/{DATASET}"
if LOCATION:
    connection_string += f"?location={LOCATION}"

engine = create_engine(connection_string)
db = SQLDatabase(engine)
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)
sql_chain = create_sql_query_chain(llm, db)

query_results = []

# ============================================================
# ツール関数
# ============================================================
def get_schema_tool() -> str:
    """テーブル情報を取得"""
    tables = db.get_usable_table_names()
    info = db.get_table_info(tables)
    return f"利用可能テーブル: {', '.join(tables)}\n\n{info}"


def create_query_tool(question: str) -> str:
    """SQLクエリを生成"""
    sql = sql_chain.invoke({"question": question})
    sql_upper = sql.upper()
    
    if "LIMIT" in sql_upper or any(k in sql_upper for k in ['COUNT(*)', 'GROUP BY']):
        return sql
    
    return f"{sql.rstrip(';').rstrip()} LIMIT {DEFAULT_LIMIT}"


def execute_query_tool(sql: str) -> str:
    """SQLを実行"""
    global query_results
    
    print(f"SQLクエリ実行中... ", end="", flush=True)
    df = pd.read_sql_query(sql, con=engine)
    print(f"✓ ({len(df)}行取得)")
    
    query_results.append({"sql": sql, "dataframe": df, "timestamp": datetime.now()})
    
    result = {
        "sql": sql,
        "行数": len(df),
        "列": list(df.columns),
        "サンプル（先頭5行）": df.head(5).to_dict(orient="records"),
        "基本統計": df.describe().to_dict() if len(df) > 0 else {}
    }
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


# ツール定義
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": "テーブル一覧とカラム情報を取得",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_query",
            "description": "質問からSQLを生成",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string", "description": "SQLに変換したい質問"}},
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_query",
            "description": "SQLを実行してデータを取得",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string", "description": "実行するSQLクエリ"}},
                "required": ["sql"]
            }
        }
    }
]

tool_functions = {
    "get_schema": get_schema_tool,
    "create_query": create_query_tool,
    "execute_query": execute_query_tool
}

# ============================================================
# エージェント実行
# ============================================================
def run_agent(question: str, max_iterations: int = 10) -> list:
    """エージェントを実行"""
    system_prompt = """あなたはBigQueryのデータアナリストです。

【手順】
1. get_schemaでテーブル構造を確認
2. create_queryでSQLを生成
3. execute_queryでデータを取得
4. データから傾向やパターンを分析

日本語で簡潔に回答してください。"""
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question}
    ]
    
    for _ in range(max_iterations):
        print("エージェント思考中...", end="", flush=True)
        response = llm.bind_tools(tools).invoke(messages)
        print(" ✓")
        
        messages.append({
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": getattr(response, 'tool_calls', None)
        })
        
        if not getattr(response, 'tool_calls', None):
            break
        
        for tool_call in response.tool_calls:
            tool_name = tool_call['name']
            tool_args = tool_call['args']
            
            print(f"ツール実行: {tool_name}")
            result = tool_functions.get(tool_name, lambda **_: "エラー")(**tool_args)
            
            messages.append({
                "role": "tool",
                "content": result,
                "tool_call_id": tool_call['id']
            })
    
    return messages


# ============================================================
# 分析・インサイト抽出
# ============================================================
def analyze_and_extract_insights(question: str) -> dict:
    """データ分析とインサイト抽出"""
    global query_results
    query_results = []
    
    print(f"\n{'='*60}\n質問: {question}\n{'='*60}\n")
    print("データ取得・分析中...\n")
    
    try:
        messages = run_agent(question)
        print("\n✓ データ取得完了")
    except Exception as e:
        print(f"\nエラー発生: {e}")
        return {"dataframes": [], "insights": f"エラー: {str(e)}", "queries": []}
    
    # インサイト抽出
    insights_text = ""
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("content") and not msg.get("tool_calls"):
            insights_text += msg["content"] + "\n"
    
    # 詳細分析
    if query_results:
        print("\n詳細分析・インサイト抽出中...")
        
        data_summary = [{
            "クエリ番号": i + 1,
            "SQL": qr["sql"],
            "行数": len(qr["dataframe"]),
            "列": list(qr["dataframe"].columns),
            "サンプル": qr["dataframe"].head(10).to_dict(orient="records")
        } for i, qr in enumerate(query_results)]
        
        analysis_prompt = f"""以下のデータを分析し、ビジネスに役立つインサイトを抽出してください。

【元の質問】{question}

【取得データ】{json.dumps(data_summary, ensure_ascii=False, indent=2, default=str)}

【分析観点】傾向、注目点、ビジネス示唆、アクションアイテム

構造化して日本語で回答してください。"""
        
        analysis_response = llm.invoke([{"role": "user", "content": analysis_prompt}])
        insights_text += "\n\n【詳細分析結果】\n" + analysis_response.content
    
    return {
        "dataframes": [qr["dataframe"] for qr in query_results],
        "insights": insights_text.strip(),
        "queries": [qr["sql"] for qr in query_results]
    }


# ============================================================
# 保存・可視化
# ============================================================
def save_analysis_results(analysis_result: dict, question: str) -> dict:
    """分析結果を保存"""
    folder = OUTPUT_DIR / "Output"
    folder.mkdir(parents=True, exist_ok=True)
    
    # インサイト保存
    with open(folder / "insights.txt", "w", encoding="utf-8") as f:
        f.write(f"質問: {question}\n")
        f.write(f"分析日時: {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}\n")
        f.write(f"{'='*60}\n\n{analysis_result['insights']}")
    
    # SQLクエリ保存
    with open(folder / "queries.sql", "w", encoding="utf-8") as f:
        for i, sql in enumerate(analysis_result["queries"], 1):
            f.write(f"-- クエリ {i}\n{sql}\n\n")
    
    # データフレーム保存
    saved_files = []
    for i, df in enumerate(analysis_result["dataframes"], 1):
        if not df.empty:
            prefix = f"data_{i}" if len(analysis_result["dataframes"]) > 1 else "data"
            df_copy = df.copy()
            
            # タイムゾーン処理
            for col in df_copy.columns:
                if pd.api.types.is_datetime64tz_dtype(df_copy[col]):
                    df_copy[col] = df_copy[col].dt.tz_localize(None)
            
            df_copy.to_csv(folder / f"{prefix}.csv", index=False)
            saved_files.append(prefix)
    
    # グラフ作成
    if analysis_result["dataframes"] and not analysis_result["dataframes"][0].empty:
        try:
            create_chart(analysis_result["dataframes"][0], folder)
        except Exception as e:
            print(f"グラフ作成エラー: {e}")
    
    return {
        "folder": str(folder),
        "insights": str(folder / "insights.txt"),
        "queries": str(folder / "queries.sql"),
        "data_files": saved_files,
        "chart": str(folder / "chart.png") if (folder / "chart.png").exists() else None
    }


def create_chart(df: pd.DataFrame, folder: Path):
    """グラフ作成"""
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    categorical_cols = df.select_dtypes(include=["object"]).columns.tolist()
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    if numeric_cols and categorical_cols:
        y_col, x_col = numeric_cols[0], categorical_cols[0]
        plot_data = (df.groupby(x_col)[y_col].sum() if df[x_col].duplicated().any() 
                     else df.set_index(x_col)[y_col]).sort_values(ascending=False).head(10)
        
        x_pos = range(len(plot_data))
        ax.bar(x_pos, plot_data.values, color='steelblue', edgecolor='black', width=0.7)
        
        for i, val in enumerate(plot_data.values):
            ax.text(i, val, f'{val:,.0f}', ha='center', va='bottom', fontsize=9)
        
        ax.set_title(f"{x_col}別の{y_col}", fontsize=14, fontweight='bold')
        ax.set_xlabel(x_col, fontsize=12)
        ax.set_ylabel(y_col, fontsize=12)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(plot_data.index, rotation=45, ha="right")
        
    elif categorical_cols:
        x_col = categorical_cols[0]
        plot_data = df[x_col].value_counts().head(10)
        
        x_pos = range(len(plot_data))
        ax.bar(x_pos, plot_data.values, color='steelblue', edgecolor='black', width=0.7)
        
        for i, val in enumerate(plot_data.values):
            ax.text(i, val, f'{val:,.0f}', ha='center', va='bottom', fontsize=9)
        
        ax.set_title(f"{x_col}の分布", fontsize=14, fontweight='bold')
        ax.set_xlabel(x_col, fontsize=12)
        ax.set_ylabel("件数", fontsize=12)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(plot_data.index, rotation=45, ha="right")
    
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x):,}'))
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    plt.savefig(folder / "chart.png", dpi=120, bbox_inches='tight')
    plt.close()


# ============================================================
# Slack投稿
# ============================================================
def post_to_slack_channel(question: str, paths: dict, insights: str, queries: list) -> bool:
    """Slackに投稿"""
    if not SLACK_TOKEN or not SLACK_CHANNEL:
        print("\nSlack設定エラー")
        return False
    
    try:
        client = WebClient(token=SLACK_TOKEN)
        
        message_text = f"""*📊 データ分析結果*

*【分析内容】*
{question[:200]}{'...' if len(question) > 200 else ''}

*【分析日時】*
{datetime.now().strftime('%Y年%m月%d日 %H:%M')}

*【インサイト】*
```
{insights[:800]}
{'...(詳細はinsights.txtを確認)' if len(insights) > 800 else ''}
```

*【実行クエリ数】*
{len(queries)}件
"""
        
        response = client.chat_postMessage(channel=SLACK_CHANNEL, text=message_text, mrkdwn=True)
        thread_ts = response["ts"]
        
        # ファイルアップロード
        files_to_upload = [('insights.txt', paths['insights']), ('queries.sql', paths['queries'])]
        
        for data_file in paths.get('data_files', []):
            csv_path = Path(paths['folder']) / f"{data_file}.csv"
            if csv_path.exists():
                files_to_upload.append((f"{data_file}.csv", str(csv_path)))
        
        if paths.get('chart') and Path(paths['chart']).exists():
            files_to_upload.append(('chart.png', paths['chart']))
        
        for filename, filepath in files_to_upload:
            if Path(filepath).exists():
                client.files_upload_v2(
                    channel=SLACK_CHANNEL,
                    file=filepath,
                    filename=filename,
                    thread_ts=thread_ts,
                    initial_comment=f"📎 {filename}"
                )
        
        print(f"\n✓ Slackに投稿完了 ({len(files_to_upload)}ファイル)")
        return True
        
    except SlackApiError as e:
        print(f"\nSlack APIエラー: {e.response['error']}")
        return False
    except Exception as e:
        print(f"\nSlack投稿エラー: {e}")
        return False


# ============================================================
# メイン関数
# ============================================================
def ask_and_analyze(question: str, post_to_slack: bool = False) -> dict:
    """質問から分析・保存・Slack投稿まで実行"""
    analysis_result = analyze_and_extract_insights(question)
    
    if not analysis_result["dataframes"] or all(df.empty for df in analysis_result["dataframes"]):
        print("\nデータが取得できませんでした")
        return {"error": "データなし"}
    
    # 結果表示
    print(f"\n{'='*60}\n📊 分析結果\n{'='*60}")
    print(f"取得データ数: {len(analysis_result['dataframes'])}件")
    print(f"実行クエリ数: {len(analysis_result['queries'])}件")
    
    for i, df in enumerate(analysis_result['dataframes'], 1):
        print(f"\nデータ{i}: {len(df)}行 × {len(df.columns)}列")
    
    print(f"\n{'='*60}\n抽出されたインサイト\n{'='*60}")
    print(analysis_result["insights"])
    print(f"{'='*60}\n")
    
    # 保存
    print("結果を保存中...")
    paths = save_analysis_results(analysis_result, question)
    print(f"✓ 保存完了: {paths['folder']}")
    
    # Slack投稿
    if post_to_slack:
        print("\nSlackに投稿中...")
        post_to_slack_channel(question, paths, analysis_result["insights"], analysis_result["queries"])
    
    return {**paths, "insights": analysis_result["insights"], "queries": analysis_result["queries"]}


# ============================================================
# 実行
# ============================================================
if __name__ == "__main__":
    question = input("質問を入力してください (例:データを分析して、有益なインサイトを抽出してください。): ")
    result = ask_and_analyze(question, post_to_slack=False) # Slack投稿なし
    # result = ask_and_analyze(question, post_to_slack=True)  # Slack投稿あり