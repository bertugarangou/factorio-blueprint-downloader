# Factorio Blueprint Downloader
_From factoriobin.com_  
Downloads blueprints and other info from factoriobin.com


## Features
- Works on Linux, Windows or macOS, you must have the `requests` dependency and Python installed.

- Creates a `backup.html` file with the books, collections and designs while keeping the hierarchy of content.
- Stores metadata (creation date, game minimum version, posted-by/author, ...)
- Saves the blueprint string to paste into the game inside `blueprint.txt`
- Saves the rendered image into `render.jpg`

## Scrits
- `factoriobin.py`: - OLD but reliable it-just-works. Downloads and shows errors.
- `factoriobin_v2.py`: **USE THIS VERSION** to download books inside books. Checks if there is a 404-file-not-found error and skips the lost image/design.

## How to use it
0. Install [python 3](https://www.python.org/downloads/)
1. Install dependencies: `pip install requests`
3. Find a design, a book or a collection (book of books of blueprints) and copy it's URL. If a URL ends with `/<number`, remove the `/<number>` (ex: https://factoriobin.com/post/cgn0od/1 -> https://factoriobin.com/post/cgn0od). Don't remove it if you want to download the full collection instead of just a design
4. Run it with:

```
python factoriobin.py <URL>
```
or
```
python3 factoriobin.py <URL>
```

Example: 
```
python factoriobin.py https://factoriobin.com/post/demo
```

It's also possible to bulk download multiple blueprints, books or collections by appending more URLs:
```
python factoriobin.py <URL1> <URL2> <...>
```

## File structure
```text
<collection-id> - <collection-name>/
├── backup.html       # Formatted HTML backup; check after downloading
├── post.json         # FactorioBin collection/post information
└── nodes/
    ├── 0001 - <book-or-design-name>/
    │   ├── blueprint.txt   # Blueprint string for Factorio
    │   ├── info.json       # FactorioBin metadata
    │   └── render.jpeg     # FactorioBin rendered image
    ├── 0002 - <book-or-design-name>/
    │   ├── blueprint.txt
    │   ├── info.json
    │   └── render.jpeg
    └── ...
```


## Screenshots

[Raynquist'](https://factoriobin.com/post/cgn0od) balancer book (fall 2025)
<img width="1570" height="915" alt="imatge" src="https://github.com/user-attachments/assets/96539d1e-665d-4711-95a1-568677fa1c2e" />

[Nilaus'](https://www.patreon.com/Nilaus) Factorio Space Age 2.1 - Final Factory:
<img width="1764" height="865" alt="imatge" src="https://github.com/user-attachments/assets/4c72b728-f6d3-4229-9cd9-757b4232d09f" />



## License
Licensed under GPL v3. GG and Thanks to [Wube Software](https://www.factorio.com/) for this amazing game and to [FactorioBin](https://factoriobin.com/about) for hosting the API.

I made this because i wanted to save old pre-1.0 blueprints from my own creation and from different YT channels without relying on websites that can just remove content or shut their service, so having an offline copy is always a good idea. The HTML menu file is a nice way to see in a non-txt way the strings and the screenshot/render.
