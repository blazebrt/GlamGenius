# Where these fixtures come from

These are **frozen, hand-built payloads shaped to the published Open Food Facts
product schema.** They are not captures of live API responses, and this file
says so plainly rather than letting the directory imply otherwise.

## Why they are not live captures

The build and CI environment for this repository reaches the network through an
egress proxy whose policy denies `world.openfoodfacts.org`,
`openfoodfacts.github.io`, `wiki.openfoodfacts.org` and
`static.openfoodfacts.org` (the proxy answers `403` to `CONNECT`). No live
product payload could be retrieved to freeze, so each fixture is assembled from
the field definitions and taxonomy entries below, which *were* retrievable.

This is the right dependency for CI in any case: a test that talks to Open Food
Facts is a test that fails when somebody else's server is slow, and a fixture
refreshed from a live product silently changes meaning when a contributor edits
that product. What the fixtures must be faithful to is the **schema**, and the
schema is what is cited here.

## Retrieved 2026-09-03

All from the `main` branch of `openfoodfacts/openfoodfacts-server`:

| What | URL |
| --- | --- |
| `categories`, `categories_tags`, `countries`, `countries_tags` | https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/docs/api/ref/schemas/product_base_tags.yaml |
| `categories_hierarchy`, `categories_lc`, `countries_hierarchy` | https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/docs/api/ref/schemas/product_tags.yaml |
| The shape of one taxonomy tag entry | https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/docs/api/ref/schemas/tags/taxonomy_tag_entry.yaml |
| The shape of one indexed (search) tag entry | https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/docs/api/ref/schemas/tags/indexed_taxonomy_tag_entry.yaml |
| The country taxonomy, for the canonical India id | https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/taxonomies/countries.txt |

The quotations that decide the design are reproduced in
`backend/app/domains/off/taxonomy.py`. The India entry in `countries.txt` reads
`en: India, Bharat, Hindustan, IN, IND` with `country_code_2:en: IN`, which is
what makes `en:india` the canonical id.

`compared_to_category` appears in none of the schema files above, so it is not
part of the documented product contract and no fixture carries it.

## The rule these fixtures exist to hold

Each file pairs a raw `categories`/`countries` text with a `categories_tags`/
`countries_tags` array, and in several files **the two disagree** — because in
the real data they do, and because every disagreement is a case where reading
the raw text gives the wrong answer. See `case_index.json` for what each one is
for.
