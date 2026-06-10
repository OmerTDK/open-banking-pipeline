"""In-process mock bank APIs serving the committed fixtures.

Each client exposes a deliberately different interaction shape (ADR-0003):
fjellvik is Berlin-Group-style paginated JSON, marlstone is FDX-style
cursor-paginated JSON, taktwerk is a whole-file CSV download.
"""
