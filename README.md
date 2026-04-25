# OCR side-by-side viewer

A static, zero-build viewer that lets a reader flip through arXiv pages and  
compare OCR outputs from multiple vision-language models on the same page.  
Vanilla HTML + CSS + ES modules. The entire repo is the deployable unit.

## Layout

```
./
  index.html          page shell
  viewer.js           ES module: fetches data/outputs.json and renders
  styles.css          responsive grid, dark-aware, print-friendly
  .nojekyll           tells GitHub Pages to serve files as-is (no Jekyll)
  data/
    outputs.json      one JSON blob describing every page + every model
    pages/*.webp      downscaled page renders referenced from outputs.json
    generate_placeholders.py   regenerates the committed placeholder WebPs
```

## Local preview

Any static HTTP server works; the simplest option is:

```bash
python -m http.server 8000
# then open http://localhost:8000
```

Serving via the filesystem (`file://`) will fail on the
`fetch('./data/outputs.json')` call in Chrome/Safari, so always use an HTTP
server during development.

## Keyboard shortcuts

- `←` / `→` &mdash; previous / next page
- `s` &mdash; shuffle to a random other page

Each model panel has a `Rendered` / `Raw` toggle that switches between the
sanitized HTML view and the plain-text view.

## Data contract

`data/outputs.json` is the single source of truth. Shape:

```jsonc
{
  "generated_at": "2026-04-19",
  "source": "modal-export",
  "models": ["chandra-ocr-2", "dots-ocr-1.5", ...],
  "pages": [
    {
      "id": "placeholder-001",
      "pdf": "2604.01234v1.pdf",
      "page": 2,
      "primary_code": "cs.LG",
      "image": "data/pages/placeholder-001.webp",
      "outputs": {
        "chandra-ocr-2": {
          "html": "<h2>...</h2>",
          "text": "plain text version",
          "latency_ms": 3200,
          "tokens": 742
        },
        "openai:gpt-5.4-mini": {
          "html": "<p>...</p>",
          "text": "plain text version",
          "latency_ms": 4100,
          "tokens": 812,
          "input_tokens": 1490,
          "output_tokens": 812,
          "total_tokens": 2302,
          "cost_usd": 0.004775
        },
        "qwen-35b-awq": {
          "html": null,
          "text": null,
          "latency_ms": null,
          "tokens": null,
          "error": "connection refused"
        }
      }
    }
  ]
}
```

Missing `outputs[model]` entries render as `(no output)` &mdash; the viewer does
not break on sparse data. Entries with `error` are rendered with an error
badge and an error body. Empty-string `html` / `text` render as `(empty output)`.
The proprietary cost probe may also include `input_tokens`, `output_tokens`,
`total_tokens`, and `cost_usd`; those fields are optional and are displayed only
when present, so older hosted-model exports continue to render normally.

### HTML sanitization

`viewer.js` runs every model's `html` field through a small allowlist
sanitizer before assigning it to `innerHTML`. The allowlist covers headings,
paragraphs, lists, simple tables, code, images, and links; any tag outside
the allowlist is unwrapped, and all `on*` event attributes plus
`javascript:` / `data:` / `vbscript:` URLs are stripped.

## Hosting on GitHub Pages

The repo is laid out to deploy as-is from the `main` branch root.

1. Commit the viewer files **and** the `data/` directory (outputs.json +

pages/*.webp) to `main`. Only `data/archive/` is gitignored; the live data is
what GitHub Pages will serve.
2. In the repo on GitHub, go to **Settings → Pages**.
3. Under **Build and deployment**, set:

- **Source**: `Deploy from a branch`
- **Branch**: `main`
- **Folder**: `/ (root)`

1. Save. Pages will publish the site at

`https://<user-or-org>.github.io/<repo>/`.

The included `.nojekyll` file tells Pages to serve files as-is, so there is
no Jekyll build step and filenames starting with `_` (if any ever appear) are
preserved.

### Custom domain

Add a `CNAME` file at the repo root containing your domain (e.g.
`ocr.example.com`) and configure the DNS record per the
[GitHub Pages custom-domain docs](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site).

## Hosting elsewhere

The folder is self-contained; any static host works with no build step:

- **Any static CDN** &mdash; Cloudflare Pages, Netlify, Vercel, `aws s3 sync`,
etc. Point the project root at this repo with no build command.
- **Embedded in another site** &mdash; copy the repo contents into your site's
public/static directory (e.g. `public/viewer/`) and link via an `<iframe src="/viewer/">` from any page on the same origin.

There is no runtime state and no server component.