def next_blocks_to_process(last_processed_id, tip, batch_limit):
    """Heights after last_processed_id, inclusive of tip, capped at batch_limit."""
    last_processed_id = int(last_processed_id)
    tip = int(tip)
    batch_limit = int(batch_limit)
    if batch_limit <= 0 or last_processed_id >= tip:
        return []
    start = last_processed_id + 1
    end = min(tip, last_processed_id + batch_limit)
    if end < start:
        return []
    return list(range(start, end + 1))


def block_hash_from_node_block(block):
    if block is None:
        return None
    value = None
    if isinstance(block, dict):
        value = block.get('hash') or block.get('blockID') or block.get('block_id')
    else:
        value = getattr(block, 'hash', None)
        if value is None:
            value = getattr(block, 'blockID', None)
        if value is None and hasattr(block, 'get'):
            value = block.get('hash') or block.get('blockID')
    if value is None:
        return None
    if hasattr(value, 'hex') and callable(value.hex):
        try:
            value = value.hex()
        except Exception:
            pass
    text = str(value)
    return text or None
