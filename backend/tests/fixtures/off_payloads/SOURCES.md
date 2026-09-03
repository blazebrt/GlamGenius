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
| `categories_hierarchy` (the non-lossy array — the authority) | https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/docs/api/ref/schemas/product_tags.yaml |
| `categories`, `categories_tags`, `countries`, `countries_tags` | https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/docs/api/ref/schemas/product_base_tags.yaml |
| `taxonomy_tag_entry` (non-lossy, kept as-is) | https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/docs/api/ref/schemas/tags/taxonomy_tag_entry.yaml |
| `indexed_taxonomy_tag_entry` (lossy, "for search only") | https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/docs/api/ref/schemas/tags/indexed_taxonomy_tag_entry.yaml |
| `compared_to_category` | https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/docs/api/ref/schemas/product_extended.yaml |
| The country taxonomy, for the canonical India id | https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/taxonomies/countries.txt |

## The field decision these fixtures encode

`categories_hierarchy` is the authority, because its own schema says it is the
non-lossy field: "categories not found in taxonomy (as-is, with no
normalization) ... should be used for display purposes, as it is not lossy."

`categories_tags` is deliberately **not** the authority. Its schema
(`indexed_taxonomy_tag_entry`) calls it "a lossy representation ... for search
only", deaccented and lowercased. Two distinct source categories can collapse
onto one indexed token, so comparing it can *manufacture* a false match — see
the collision pair below.

`compared_to_category` **exists** (`product_extended.yaml`: "the category to use
for comparison. **TODO** explain how it is chosen."). Because its own schema
carries a TODO for how it is generated, and the server assigns it from the lossy
`categories_tags`, it is not defensible as our comparison authority. It was
investigated and deliberately not used; no fixture depends on it.

The India entry in `countries.txt` reads `en: India, Bharat, Hindustan, IN, IND`
with `country_code_2:en: IN`, which is what makes `en:india` the canonical id.

## The rule these fixtures exist to hold

Each file pairs a raw `categories`/`countries` text with the taxonomy arrays,
and in several files **they disagree** — because in the real data they do, and
because every disagreement is a case where reading the wrong field gives the
wrong answer. See `case_index.json` for what each one is for. Cases 11 and 12
are the load-bearing pair: their `categories_hierarchy` values are distinct
source strings while their `categories_tags` tokens are identical, so a
comparison on `categories_tags` would call two different products the same and a
comparison on `categories_hierarchy` correctly does not.
