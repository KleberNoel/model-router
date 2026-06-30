from pathlib import Path


def replace_once(text: str, old: str, new: str, path: Path) -> str:
    if old not in text:
        raise RuntimeError(f"expected snippet not found in {path}")
    return text.replace(old, new, 1)


HELPERS_OLD = """log = logging.getLogger(__name__)\n"""


HELPERS_NEW = """log = logging.getLogger(__name__)\n\nINLINE_FULL_CONTEXT_MAX_CHARS = int(os.getenv('OPENWEBUI_INLINE_FULL_CONTEXT_MAX_CHARS', '16000'))\nINLINE_FULL_CONTEXT_EXTENSIONS = {\n    '.txt',\n    '.md',\n    '.markdown',\n    '.rst',\n    '.csv',\n    '.tsv',\n    '.json',\n    '.jsonl',\n    '.xml',\n    '.yaml',\n    '.yml',\n    '.log',\n    '.ini',\n    '.cfg',\n    '.toml',\n    '.py',\n    '.js',\n    '.ts',\n    '.tsx',\n    '.jsx',\n    '.html',\n    '.htm',\n    '.css',\n    '.sql',\n}\nINLINE_FULL_CONTEXT_CONTENT_TYPES = (\n    'text/',\n    'application/json',\n    'application/ld+json',\n    'application/xml',\n    'application/javascript',\n    'application/x-javascript',\n    'application/yaml',\n    'application/x-yaml',\n    'application/x-ndjson',\n)\n\n\ndef _merge_item_metadata(target: dict, source) -> dict:\n    if isinstance(source, dict):\n        target.update(source)\n    return target\n\n\ndef get_item_file_meta(item: dict, file_object=None) -> dict:\n    meta = {}\n    file_info = item.get('file') or {}\n    _merge_item_metadata(meta, file_info.get('meta'))\n    _merge_item_metadata(meta, (file_info.get('data') or {}).get('metadata'))\n    _merge_item_metadata(meta, getattr(file_object, 'meta', None))\n    return meta\n\n\ndef _get_item_name(item: dict, file_object=None) -> str:\n    meta = get_item_file_meta(item, file_object)\n    return item.get('name') or meta.get('name') or getattr(file_object, 'filename', '') or ''\n\n\ndef _get_item_content_type(item: dict, file_object=None) -> str:\n    meta = get_item_file_meta(item, file_object)\n    return str(meta.get('content_type') or meta.get('type') or '').lower()\n\n\ndef _get_item_size(item: dict, content=None, file_object=None):\n    meta = get_item_file_meta(item, file_object)\n    size = meta.get('size')\n\n    if isinstance(size, str) and size.isdigit():\n        size = int(size)\n\n    if isinstance(size, int):\n        return size\n\n    if isinstance(content, str):\n        return len(content)\n\n    return None\n\n\ndef item_uses_full_context(\n    item: dict,\n    *,\n    bypass_embedding_and_retrieval: bool = False,\n    content=None,\n    file_object=None,\n) -> bool:\n    if item.get('context') != 'full' and not bypass_embedding_and_retrieval:\n        return False\n\n    if item.get('type') not in {'text', 'file', 'collection'}:\n        return True\n\n    if item.get('type') == 'collection':\n        return True\n\n    size = _get_item_size(item, content=content, file_object=file_object)\n    if isinstance(size, int) and size > INLINE_FULL_CONTEXT_MAX_CHARS:\n        return False\n\n    name = _get_item_name(item, file_object).lower()\n    content_type = _get_item_content_type(item, file_object)\n\n    if any(name.endswith(ext) for ext in INLINE_FULL_CONTEXT_EXTENSIONS):\n        return True\n\n    if any(content_type.startswith(prefix) for prefix in INLINE_FULL_CONTEXT_CONTENT_TYPES):\n        return True\n\n    return item.get('type') == 'text' and isinstance(content, str) and len(content) <= INLINE_FULL_CONTEXT_MAX_CHARS\n"""


TEXT_FULL_CONTEXT_OLD = """            if item.get('context') == 'full':\n                if item.get('file'):\n                    # if item has file data, use it\n                    query_result = {\n                        'documents': [[item.get('file', {}).get('data', {}).get('content')]],\n                        'metadatas': [[item.get('file', {}).get('meta', {})]],\n                    }\n"""


TEXT_FULL_CONTEXT_NEW = """            text_content = item.get('file', {}).get('data', {}).get('content')\n            if item_uses_full_context(item, content=text_content):\n                if item.get('file'):\n                    # if item has file data, use it\n                    query_result = {\n                        'documents': [[text_content]],\n                        'metadatas': [[item.get('file', {}).get('meta', {})]],\n                    }\n"""


FILE_BLOCK_OLD = """        elif item.get('type') == 'file':\n            if item.get('context') == 'full' or request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL:\n                if item.get('file', {}).get('data', {}).get('content', ''):\n                    # Manual Full Mode Toggle\n                    # Used from chat file modal, we can assume that the file content will be available from item.get(\"file\").get(\"data\", {}).get(\"content\")\n                    query_result = {\n                        'documents': [[item.get('file', {}).get('data', {}).get('content', '')]],\n                        'metadatas': [\n                            [\n                                {\n                                    'file_id': item.get('id'),\n                                    'name': item.get('name'),\n                                    **item.get('file').get('data', {}).get('metadata', {}),\n                                }\n                            ]\n                        ],\n                    }\n                elif item.get('id'):\n                    file_object = await Files.get_file_by_id(item.get('id'))\n                    if file_object and (\n                        user.role == 'admin'\n                        or file_object.user_id == user.id\n                        or await has_access_to_file(item.get('id'), 'read', user)\n                    ):\n                        query_result = {\n                            'documents': [[file_object.data.get('content', '')]],\n                            'metadatas': [\n                                [\n                                    {\n                                        'file_id': item.get('id'),\n                                        'name': file_object.filename,\n                                        'source': file_object.filename,\n                                    }\n                                ]\n                            ],\n                        }\n            else:\n"""


FILE_BLOCK_NEW = """        elif item.get('type') == 'file':\n            file_content = item.get('file', {}).get('data', {}).get('content', '')\n            use_full_context = item_uses_full_context(\n                item,\n                bypass_embedding_and_retrieval=request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL,\n                content=file_content,\n            )\n\n            if use_full_context:\n                if file_content:\n                    # Manual Full Mode Toggle\n                    # Used from chat file modal, we can assume that the file content will be available from item.get(\"file\").get(\"data\", {}).get(\"content\")\n                    query_result = {\n                        'documents': [[file_content]],\n                        'metadatas': [\n                            [\n                                {\n                                    'file_id': item.get('id'),\n                                    'name': item.get('name'),\n                                    **item.get('file').get('data', {}).get('metadata', {}),\n                                }\n                            ]\n                        ],\n                    }\n                elif item.get('id'):\n                    file_object = await Files.get_file_by_id(item.get('id'))\n                    if file_object and (\n                        user.role == 'admin'\n                        or file_object.user_id == user.id\n                        or await has_access_to_file(item.get('id'), 'read', user)\n                    ):\n                        file_object_content = file_object.data.get('content', '')\n                        if item_uses_full_context(\n                            item,\n                            bypass_embedding_and_retrieval=request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL,\n                            content=file_object_content,\n                            file_object=file_object,\n                        ):\n                            query_result = {\n                                'documents': [[file_object_content]],\n                                'metadatas': [\n                                    [\n                                        {\n                                            'file_id': item.get('id'),\n                                            'name': file_object.filename,\n                                            'source': file_object.filename,\n                                        }\n                                    ]\n                                ],\n                            }\n            else:\n"""


COLLECTION_FULL_CONTEXT_OLD = """                if item.get('context') == 'full' or request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL:\n"""


COLLECTION_FULL_CONTEXT_NEW = """                if item_uses_full_context(\n                    item,\n                    bypass_embedding_and_retrieval=request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL,\n                ):\n"""


COLLECTION_QUERY_OLD = """                if full_context:\n                    # Sync helper makes blocking VECTOR_DB_CLIENT calls;\n                    # offload so the async caller's event loop stays free.\n                    query_result = await asyncio.to_thread(get_all_items_from_collections, collection_names)\n                else:\n                    query_result = await query_collection(\n"""


COLLECTION_QUERY_NEW = """                if full_context and item_uses_full_context(\n                    item,\n                    bypass_embedding_and_retrieval=request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL,\n                ):\n                    # Sync helper makes blocking VECTOR_DB_CLIENT calls;\n                    # offload so the async caller's event loop stays free.\n                    query_result = await asyncio.to_thread(get_all_items_from_collections, collection_names)\n                else:\n                    query_result = await query_collection(\n"""


MIDDLEWARE_IMPORT_OLD = """from open_webui.retrieval.utils import get_sources_from_items\n"""


MIDDLEWARE_IMPORT_NEW = """from open_webui.retrieval.utils import get_sources_from_items, item_uses_full_context\n"""


MIDDLEWARE_FULL_CONTEXT_OLD = """        # Check if all files are in full context mode\n        all_full_context = all(item.get('context') == 'full' for item in files)\n"""


MIDDLEWARE_FULL_CONTEXT_NEW = """        # Only keep full-context mode for small text-like attachments.\n        all_full_context = all(\n            item_uses_full_context(\n                item,\n                bypass_embedding_and_retrieval=request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL,\n            )\n            for item in files\n        )\n"""


def patch_retrieval_utils(path: Path) -> None:
    text = path.read_text()
    text = replace_once(text, HELPERS_OLD, HELPERS_NEW, path)
    text = replace_once(text, TEXT_FULL_CONTEXT_OLD, TEXT_FULL_CONTEXT_NEW, path)
    text = replace_once(text, FILE_BLOCK_OLD, FILE_BLOCK_NEW, path)
    text = replace_once(text, COLLECTION_FULL_CONTEXT_OLD, COLLECTION_FULL_CONTEXT_NEW, path)
    text = replace_once(text, COLLECTION_QUERY_OLD, COLLECTION_QUERY_NEW, path)
    path.write_text(text)


def patch_middleware(path: Path) -> None:
    text = path.read_text()
    text = replace_once(text, MIDDLEWARE_IMPORT_OLD, MIDDLEWARE_IMPORT_NEW, path)
    text = replace_once(text, MIDDLEWARE_FULL_CONTEXT_OLD, MIDDLEWARE_FULL_CONTEXT_NEW, path)
    path.write_text(text)


def main() -> None:
    patch_retrieval_utils(Path('/app/backend/open_webui/retrieval/utils.py'))
    patch_middleware(Path('/app/backend/open_webui/utils/middleware.py'))


if __name__ == '__main__':
    main()
