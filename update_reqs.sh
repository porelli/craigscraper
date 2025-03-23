pipreqs --ignore __pycache_,.github,.venv,.vscode,bin,include,lib,lib64 --encoding utf-8 --force .
echo "scrapy-fake-useragent==$(pip index versions scrapy-fake-useragent 2>/dev/null | egrep -o '([0-9]+\.){2}[0-9]+' | head -n 1)" >> requirements.txt # needs explicit add: https://github.com/bndr/pipreqs/pull/377
