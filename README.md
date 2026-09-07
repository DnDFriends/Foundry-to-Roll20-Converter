# Foundry → Roll20 Converter — GitHub Pages

This folder is a complete static website. It runs the Python conversion engine in the visitor's browser with Pyodide; selected character files are not uploaded to a server.

## Publish as a new GitHub repository

1. Create a public repository on GitHub, for example `foundry-roll20-converter`.
2. Upload `index.html`, `converter.py`, and `.nojekyll` from this folder to the repository root.
3. Open **Settings → Pages**.
4. Under **Build and deployment**, choose **Deploy from a branch**.
5. Select the `main` branch and `/ (root)`, then click **Save**.

The site will normally appear at:

`https://YOUR-USERNAME.github.io/foundry-roll20-converter/`

For the `DnDFriends` account and that repository name, the address would be:

`https://dndfriends.github.io/foundry-roll20-converter/`

GitHub Pages may take a few minutes to publish after a change. Keep `converter.py` beside `index.html`; the page loads it by that exact relative filename.

## Update the site

Replace `index.html` or `converter.py` in the repository and commit the changes. GitHub Pages republishes the site automatically.

## Local testing

Browsers block some files when `index.html` is opened directly. Run a small local server from this folder:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000`.

The first visit downloads the browser Python runtime from jsDelivr. After that, conversion happens locally in the page.
