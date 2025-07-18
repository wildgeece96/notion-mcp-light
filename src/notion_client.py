"""
Notionクライアントモジュール

NotionのAPIを利用してMarkdownファイルのアップロードとNotionページのダウンロードを行うクラスを提供します。
"""

import os
import time
from typing import List, Dict, Any, Optional
from pathlib import Path
from notion_client import Client
from notion_client.errors import APIResponseError, RequestTimeoutError
from dotenv import load_dotenv

from src.markdown_converter import MarkdownConverter


class NotionBlockLimitError(Exception):
    """Raised when content exceeds Notion's block limits."""
    pass


class NotionChunkingError(Exception):
    """Raised when block chunking fails."""
    pass


class NotionClient:
    """
    NotionのAPIを利用してMarkdownファイルのアップロードとNotionページのダウンロードを行うクラス

    Attributes:
        client: Notion APIクライアント
        converter: MarkdownConverterのインスタンス
    """

    def __init__(self, token: Optional[str] = None, max_blocks_per_request: int = 100, 
                 rate_limit_delay: float = 0.4, max_total_blocks: int = 1000):
        """
        NotionClientのコンストラクタ

        Args:
            token: Notion API Token（指定されない場合は環境変数から取得）
            max_blocks_per_request: 1リクエストあたりの最大ブロック数
            rate_limit_delay: リクエスト間の待機時間（秒）
            max_total_blocks: 1ページあたりの最大ブロック数
        """
        # 環境変数からトークンを取得
        load_dotenv()
        self.token = token or os.getenv("NOTION_TOKEN")

        if not self.token:
            raise ValueError(
                "Notion API Tokenが指定されていません。"
                "環境変数NOTION_TOKENを設定してください。"
            )

        self.client = Client(auth=self.token)
        self.converter = MarkdownConverter()
        self.max_blocks_per_request = max_blocks_per_request
        self.rate_limit_delay = rate_limit_delay
        self.max_total_blocks = max_total_blocks

    def upload_markdown(self, filepath: str, database_id: Optional[str] = None, 
                       page_id: Optional[str] = None) -> str:
        """
        Markdownファイルを読み込み、Notionページとしてアップロードします。
        ブロック数が100を超える場合は自動的にチャンク処理を行います。

        Args:
            filepath: アップロードするMarkdownファイルのパス
            database_id: アップロード先のデータベースID
            page_id: 親ページID（database_idが指定されていない場合に使用）

        Returns:
            作成されたNotionページのID
        """
        # ファイルパスのバリデーション
        self._validate_file_path(filepath)
        
        # ファイルを読み込む
        file_path = Path(filepath)
        if not file_path.exists():
            raise FileNotFoundError(f"ファイルが見つかりません: {filepath}")

        with open(file_path, "r", encoding="utf-8") as f:
            markdown_content = f.read()

        # Markdownをパースしてブロックに変換
        blocks, title = self.converter.parse_markdown_to_blocks(markdown_content)

        # ブロック数の制限チェック
        if len(blocks) > self.max_total_blocks:
            raise NotionBlockLimitError(
                f"ブロック数が制限を超えています: {len(blocks)} > {self.max_total_blocks}"
            )

        # ブロック数が100を超える場合は自動的にチャンク処理を使用
        if len(blocks) > self.max_blocks_per_request:
            return self._upload_markdown_with_chunks(blocks, title, database_id, page_id)
        
        # ページを作成（通常処理）
        if database_id:
            # データベース内にページを作成
            page = self.client.pages.create(
                parent={"database_id": database_id},
                properties={"title": {"title": [{"text": {"content": title}}]}},
                children=blocks,
            )
        elif page_id:
            # 親ページの下に新規ページを作成
            page = self.client.pages.create(
                parent={"page_id": page_id},
                properties={"title": {"title": [{"text": {"content": title}}]}},
                children=blocks,
            )
        else:
            # 親ページIDが指定されていない場合は、エラーを発生させる
            raise ValueError(
                "database_idまたはpage_idを指定してください。ワークスペースに直接ページを作成することはできません。"
            )

        return page["id"]

    def _upload_markdown_with_chunks(self, blocks: List[Dict[str, Any]], title: str, database_id: Optional[str] = None, page_id: Optional[str] = None) -> str:
        """
        ブロックをチャンク処理してページを作成します。

        Args:
            blocks: アップロードするブロックのリスト
            title: ページタイトル
            database_id: アップロード先のデータベースID
            page_id: 親ページID

        Returns:
            作成されたNotionページのID
        """
        # ページを作成（初期は空で作成）
        if database_id:
            # データベース内にページを作成
            page = self.client.pages.create(
                parent={"database_id": database_id},
                properties={"title": {"title": [{"text": {"content": title}}]}}
            )
        elif page_id:
            # 親ページの下に新規ページを作成
            page = self.client.pages.create(
                parent={"page_id": page_id},
                properties={"title": {"title": [{"text": {"content": title}}]}}
            )
        else:
            raise ValueError(
                "database_idまたはpage_idを指定してください。"
                "ワークスペースに直接ページを作成することはできません。"
            )

        # ブロックをチャンクごとに追加
        if blocks:
            self._upload_blocks_chunked(page["id"], blocks)

        return page["id"]

    def _chunk_blocks(self, blocks: List[Dict[str, Any]], chunk_size: int = 100) -> List[List[Dict[str, Any]]]:
        """
        ブロックリストを指定されたサイズでチャンクに分割します。

        Args:
            blocks: 分割するブロックのリスト
            chunk_size: チャンクのサイズ（デフォルト: 100）

        Returns:
            チャンクに分割されたブロックのリスト
        """
        return [blocks[i:i + chunk_size] for i in range(0, len(blocks), chunk_size)]

    def _upload_blocks_chunked(self, page_id: str, blocks: List[Dict[str, Any]]) -> None:
        """
        ブロックを100個ずつチャンクに分けてページに追加します。

        Args:
            page_id: 追加先のページID
            blocks: 追加するブロックのリスト
        """
        if not blocks:
            return

        chunks = self._chunk_blocks(blocks, self.max_blocks_per_request)
        
        for i, chunk in enumerate(chunks):
            try:
                self.client.blocks.children.append(
                    block_id=page_id,
                    children=chunk
                )
                
                # レート制限対応: リクエスト間に短い待機時間を設ける
                if i < len(chunks) - 1:  # 最後のチャンク以外
                    time.sleep(self.rate_limit_delay)
                    
            except (APIResponseError, RequestTimeoutError) as e:
                raise NotionChunkingError(
                    f"ブロックの追加中にエラーが発生しました "
                    f"(chunk {i+1}/{len(chunks)}): {str(e)}"
                )


    def append_to_page(self, page_id: str, markdown_content: str) -> None:
        """
        既存のNotionページにMarkdownコンテンツを追記します。

        Args:
            page_id: 追記先のページID
            markdown_content: 追記するMarkdownコンテンツ
        
        Raises:
            NotionBlockLimitError: ブロック数が制限を超える場合
            NotionChunkingError: ブロックの追加に失敗した場合
        """
        # Markdownをパースしてブロックに変換
        blocks, _ = self.converter.parse_markdown_to_blocks(markdown_content)
        
        # ブロック数の制限チェック
        if len(blocks) > self.max_total_blocks:
            raise NotionBlockLimitError(
                f"ブロック数が制限を超えています: {len(blocks)} > {self.max_total_blocks}"
            )
        
        # ブロックをチャンクごとに追加
        if blocks:
            self._upload_blocks_chunked(page_id, blocks)

    def update_page_content(self, page_id: str, markdown_content: str) -> None:
        """
        既存のNotionページの内容を完全に置き換えます。

        Args:
            page_id: 更新するページID
            markdown_content: 新しいMarkdownコンテンツ
        
        Raises:
            NotionBlockLimitError: ブロック数が制限を超える場合
            NotionChunkingError: ブロックの操作に失敗した場合
        """
        # 既存のブロックを取得
        existing_blocks = []
        has_more = True
        cursor = None

        while has_more:
            try:
                if cursor:
                    response = self.client.blocks.children.list(
                        block_id=page_id, start_cursor=cursor
                    )
                else:
                    response = self.client.blocks.children.list(block_id=page_id)

                existing_blocks.extend(response["results"])
                has_more = response["has_more"]
                cursor = response.get("next_cursor")
            except (APIResponseError, RequestTimeoutError) as e:
                raise NotionChunkingError(
                    f"ブロックの取得に失敗しました: {str(e)}"
                )

        # 既存のブロックを削除
        for block in existing_blocks:
            try:
                self.client.blocks.delete(block_id=block["id"])
                time.sleep(0.1)  # 削除リクエスト間の待機
            except (APIResponseError, RequestTimeoutError) as e:
                print(
                    f"Warning: ブロック {block['id']} の削除に失敗しました: {str(e)}"
                )

        # 新しいコンテンツを追加
        self.append_to_page(page_id, markdown_content)

    def _validate_file_path(self, filepath: str) -> None:
        """
        ファイルパスのセキュリティバリデーションを行います。
        
        Args:
            filepath: 検証するファイルパス
            
        Raises:
            ValueError: 不正なファイルパスの場合
        """
        file_path = Path(filepath)
        
        # 絶対パスの場合、特定のディレクトリ以下に限定
        if file_path.is_absolute():
            # 現在の作業ディレクトリ以下のファイルのみ許可
            try:
                file_path.resolve().relative_to(Path.cwd().resolve())
            except ValueError:
                raise ValueError(f"不正なファイルパス: {filepath}")
        
        # 親ディレクトリへのアクセスを防ぐ
        if ".." in file_path.parts:
            raise ValueError(f"親ディレクトリへのアクセスは許可されていません: {filepath}")
        
        # ファイルサイズの制限チェック（10MB）
        max_size = 10 * 1024 * 1024  # 10MB
        if file_path.exists() and file_path.stat().st_size > max_size:
            raise ValueError(f"ファイルサイズが制限を超えています: {filepath}")

    def download_page(self, page_id: str, output_path: str) -> None:
        """
        NotionページをダウンロードしてMarkdownファイルとして保存します。

        Args:
            page_id: ダウンロードするNotionページのID
            output_path: 出力先のファイルパス
        """
        # ページ情報を取得
        page = self.client.pages.retrieve(page_id)

        # ページタイトルを取得
        title = ""
        title_property = page["properties"].get("title", {})
        if title_property and "title" in title_property:
            title_items = title_property["title"]
            if title_items:
                title = title_items[0].get("plain_text", "")

        # ブロックを取得
        blocks = []
        has_more = True
        cursor = None

        while has_more:
            if cursor:
                response = self.client.blocks.children.list(block_id=page_id, start_cursor=cursor)
            else:
                response = self.client.blocks.children.list(block_id=page_id)

            blocks.extend(response["results"])
            has_more = response["has_more"]
            cursor = response.get("next_cursor")

        # ブロックをMarkdownに変換
        markdown_content = self.converter.convert_blocks_to_markdown(blocks, title)

        # ファイルに保存
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)

    def get_database_pages(self, database_id: str) -> List[Dict[str, Any]]:
        """
        データベース内のすべてのページを取得します。

        Args:
            database_id: 取得するデータベースのID

        Returns:
            データベース内のページのリスト
        """
        pages = []
        has_more = True
        cursor = None

        while has_more:
            if cursor:
                response = self.client.databases.query(database_id=database_id, start_cursor=cursor)
            else:
                response = self.client.databases.query(database_id=database_id)

            pages.extend(response["results"])
            has_more = response["has_more"]
            cursor = response.get("next_cursor")

        return pages

    def download_database(self, database_id: str, output_dir: str) -> None:
        """
        データベース内のすべてのページをダウンロードしてMarkdownファイルとして保存します。

        Args:
            database_id: ダウンロードするデータベースのID
            output_dir: 出力先のディレクトリパス
        """
        # データベース内のページを取得
        pages = self.get_database_pages(database_id)

        # 出力ディレクトリを作成
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # 各ページをダウンロード
        for page in pages:
            page_id = page["id"]

            # ページタイトルを取得
            title = ""
            title_property = page["properties"].get("title", {})
            if title_property and "title" in title_property:
                title_items = title_property["title"]
                if title_items:
                    title = title_items[0].get("plain_text", "")

            # ファイル名を生成（タイトルがない場合はページIDを使用）
            filename = f"{title or page_id}.md"
            file_path = output_path / filename

            # ページをダウンロード
            self.download_page(page_id, str(file_path))
