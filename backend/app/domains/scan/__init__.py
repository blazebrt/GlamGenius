"""V2 scan domain: PostgreSQL-backed photo analysis history.

**No image base64 is ever persisted.** Face/hair/hands scan photos are
analysed once and dropped. Only the structured analysis, model provenance and
consent state are kept.
"""
