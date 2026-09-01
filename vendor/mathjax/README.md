# Vendored MathJax

Pinned from the official `mathjax@3.2.2` npm package (npm shasum
`c754d7b46a679d7f3fa03543d6b8bf124ddf9f6b`). Retained: the combined
`tex-mml-chtml.js` component, its CHTML WOFF fonts, the `input/tex/extensions/`
directory, and the upstream Apache-2.0 license.

`input/tex/extensions/` is load-bearing, not optional. `tex-mml-chtml.js`
ships only MathJax's default TeX package set; anything outside it -- most
commonly `\boldsymbol`, but also `\bm`, `\cancel`, `\enclose`, `\href` --
is fetched lazily at typeset time from `[tex]/extensions/<name>.js`. When
that fetch 404s the typeset promise REJECTS, so a single unvendored macro
silences **every** expression on the page, not just its own. Seven briefs
were served math-free that way before the directory was added.

Run `(cd vendor/mathjax && shasum -a 256 -c SHA256SUMS)` to verify the files;
the renderer performs the same check before copying assets beside a brief.
Regenerate the manifest after any change to the tree:

    cd vendor/mathjax
    find . -type f ! -name SHA256SUMS ! -name README.md | sed 's|^\./||' \
      | LC_ALL=C sort > /tmp/mjx-files
    xargs shasum -a 256 < /tmp/mjx-files > SHA256SUMS

`verify_mathjax_vendor()` fails closed on an unmanifested *or* missing file,
so the manifest and the tree must be updated in the same commit.
