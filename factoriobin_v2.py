#!/usr/bin/env python3

import argparse
import base64
import html
import json
import mimetypes
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests


# ============================================================
# Configuration
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "FactorioBin-Backup/2.0"
})

TIMEOUT = 30
RETRIES = 3
RETRY_DELAY = 2


# ============================================================
# HTTP helpers
# ============================================================

def request_with_retries(method, url, **kwargs):
    """
    Make an HTTP request with retries for transient failures.

    Important:
    - 4xx responses are client errors and are NOT retried.
    - FactorioBin uses HTTP 404 when a node index does not exist,
      so retrying a 404 would only waste time.
    - Network errors and 5xx server errors are retried.
    """
    last_error = None

    for attempt in range(1, RETRIES + 1):
        try:
            response = SESSION.request(
                method,
                url,
                timeout=TIMEOUT,
                **kwargs
            )

            # Client errors (400-499) are not transient.
            # In particular, FactorioBin uses 404 to indicate that
            # a requested node does not exist.
            if 400 <= response.status_code < 500:
                response.raise_for_status()

            # Server errors (500-599) are considered transient.
            response.raise_for_status()

            return response

        except requests.HTTPError as e:
            last_error = e

            # Never retry client errors such as 404.
            if (
                e.response is not None
                and 400 <= e.response.status_code < 500
            ):
                raise

            if attempt < RETRIES:
                print(
                    f"    ! Request failed "
                    f"(attempt {attempt}/{RETRIES}): {e}"
                )
                time.sleep(RETRY_DELAY)

        except requests.RequestException as e:
            last_error = e

            if attempt < RETRIES:
                print(
                    f"    ! Request failed "
                    f"(attempt {attempt}/{RETRIES}): {e}"
                )
                time.sleep(RETRY_DELAY)

    raise last_error


def get_json(url):
    response = request_with_retries("GET", url)
    return response.json()


def get_bytes(url):
    response = request_with_retries("GET", url)

    return (
        response.content,
        response.headers.get("Content-Type", "")
    )


# ============================================================
# FactorioBin URL handling
# ============================================================

def parse_post_url(url):
    """
    Supports:

        https://factoriobin.com/post/demo
        https://factoriobin.com/post/demo/23
        https://factoriobin.com/post/demo/23/
    """

    parsed = urlparse(url)

    if parsed.netloc not in (
        "factoriobin.com",
        "www.factoriobin.com"
    ):
        raise ValueError(
            f"Not a FactorioBin URL: {url}"
        )

    parts = [
        x for x in parsed.path.split("/")
        if x
    ]

    if len(parts) < 2 or parts[0] != "post":
        raise ValueError(
            f"Expected /post/<id>[/<node>]: {url}"
        )

    post_id = parts[1]

    node_index = None

    if len(parts) >= 3:
        try:
            node_index = int(parts[2])
        except ValueError:
            raise ValueError(
                f"Invalid node index: {parts[2]}"
            )

    return post_id, node_index


# ============================================================
# FactorioBin info.json
# ============================================================

def get_node_info(post_id, node_index=None):

    if node_index is None:
        url = (
            f"https://factoriobin.com/"
            f"post/{post_id}/info.json"
        )
    else:
        url = (
            f"https://factoriobin.com/"
            f"post/{post_id}/{node_index}/info.json"
        )

    return get_json(url)


def get_all_nodes(post_id):
    """
    Get all nodes from a FactorioBin post.

    Node indexes normally start at 0 and increase
    sequentially. We continue until FactorioBin
    returns 404.

    A malformed node is skipped rather than killing
    the complete backup.
    """

    nodes = []

    index = 0

    while True:

        try:
            data = get_node_info(
                post_id,
                index
            )

        except requests.HTTPError as e:

            if (
                e.response is not None
                and e.response.status_code == 404
            ):
                break

            print(
                f"    ! Failed reading node "
                f"#{index}: {e}"
            )

            index += 1
            continue

        except Exception as e:

            print(
                f"    ! Failed reading node "
                f"#{index}: {e}"
            )

            index += 1
            continue

        if not isinstance(data, dict):
            print(
                f"    ! Node #{index} returned "
                f"invalid JSON structure"
            )

            index += 1
            continue

        node = data.get("node")

        if not isinstance(node, dict):
            print(
                f"    ! Node #{index} has no valid "
                f"'node' object"
            )

            index += 1
            continue

        # Make sure the index exists.
        # If FactorioBin omitted it, use the requested index.
        if node.get("index") is None:
            node["index"] = index

        nodes.append(data)

        index += 1

    return nodes


# ============================================================
# File helpers
# ============================================================

def safe_filename(name):

    name = name or "Untitled"

    name = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        str(name)
    )

    name = re.sub(
        r"\s+",
        " ",
        name
    ).strip()

    return name[:180] or "Untitled"


def data_uri(data, content_type):

    if not content_type:
        content_type = "image/jpeg"

    if ";" in content_type:
        content_type = content_type.split(
            ";",
            1
        )[0]

    encoded = base64.b64encode(data).decode(
        "ascii"
    )

    return (
        f"data:{content_type};base64,"
        f"{encoded}"
    )


# ============================================================
# Blueprint string
# ============================================================

def download_blueprint(node, directory):

    if not isinstance(node, dict):
        return None

    url = node.get(
        "blueprintStringUrl"
    )

    if not url:
        return None

    data, _ = get_bytes(url)

    path = directory / "blueprint.txt"

    path.write_bytes(data)

    return path


# ============================================================
# Render image
# ============================================================

def download_render(node, directory):

    if not isinstance(node, dict):
        return None, None

    url = node.get(
        "renderImageUrl"
    )

    if not url:
        return None, None

    try:
        data, content_type = get_bytes(url)

    except Exception as e:

        print(
            f"    ! Render unavailable: "
            f"{url}"
        )

        print(
            f"      {e}"
        )

        return None, None

    extension = ".jpg"

    if content_type:

        guessed = mimetypes.guess_extension(
            content_type.split(
                ";",
                1
            )[0]
        )

        if guessed:
            extension = guessed

    path = directory / f"render{extension}"

    path.write_bytes(data)

    return (
        path,
        data_uri(
            data,
            content_type
        )
    )


# ============================================================
# Node tree
# ============================================================

def build_tree(nodes):
    """
    Converts FactorioBin's flat node list:

        index
        parentIndex

    into a nested tree.

    This function is deliberately defensive because
    some FactorioBin nodes can have null/missing data.
    """

    by_index = {}

    for data in nodes:

        if not isinstance(data, dict):
            continue

        node = data.get("node")

        if not isinstance(node, dict):
            continue

        index = node.get("index")

        if index is None:
            continue

        by_index[index] = node

    children = {}

    for node in by_index.values():

        parent = node.get(
            "parentIndex"
        )

        if parent is None:
            continue

        # Only attach to an existing node.
        # This prevents malformed parent references
        # from disappearing into nowhere.
        if parent not in by_index:
            continue

        children.setdefault(
            parent,
            []
        ).append(node)

    def make_node(node):

        result = dict(node)

        child_nodes = children.get(
            node.get("index"),
            []
        )

        child_nodes = sorted(
            child_nodes,
            key=lambda x: (
                x.get("index")
                if isinstance(
                    x.get("index"),
                    int
                )
                else 0
            )
        )

        result["children"] = [
            make_node(child)
            for child in child_nodes
        ]

        return result

    roots = [
        node
        for node in by_index.values()
        if node.get("parentIndex") is None
        or node.get("parentIndex")
        not in by_index
    ]

    roots = sorted(
        roots,
        key=lambda x: (
            x.get("index")
            if isinstance(
                x.get("index"),
                int
            )
            else 0
        )
    )

    return [
        make_node(root)
        for root in roots
    ]


# ============================================================
# HTML helpers
# ============================================================

def esc(value):

    if value is None:
        return ""

    return html.escape(
        str(value)
    )


def node_type_label(node):

    if not isinstance(node, dict):
        return "Unknown"

    node_type = node.get(
        "type"
    )

    return {
        "blueprint-book": "📚 Blueprint Book",
        "blueprint": "🔷 Blueprint",
        "upgrade-planner": "⬆️ Upgrade Planner",
        "deconstruction-planner":
            "🧹 Deconstruction Planner",
    }.get(
        node_type,
        node_type or "Unknown"
    )


def render_node(
    node,
    assets,
    level=0
):

    if not isinstance(node, dict):
        return ""

    index = node.get(
        "index"
    )

    if index is None:
        return ""

    asset = assets.get(
        index,
        {}
    )

    if not isinstance(asset, dict):
        asset = {}

    name = (
        node.get("name")
        or "Untitled"
    )

    description = node.get(
        "description"
    )

    node_type = node_type_label(
        node
    )

    # --------------------------------------------------------
    # Image
    # --------------------------------------------------------

    image_html = ""

    image_data = asset.get(
        "image_data"
    )

    if image_data:

        image_html = f"""
        <div class="preview">
            <img
                src="{image_data}"
                alt="{esc(name)} render"
                loading="lazy"
            >
        </div>
        """

    # --------------------------------------------------------
    # Stats
    # --------------------------------------------------------

    stats = []

    factorio_version = node.get(
        "factorioVersion"
    )

    if factorio_version:

        stats.append(
            f"<b>Factorio:</b> "
            f"{esc(factorio_version)}"
        )

    if node.get(
        "numEntities"
    ) is not None:

        stats.append(
            f"<b>Entities:</b> "
            f"{esc(node.get('numEntities'))}"
        )

    if node.get(
        "numRequests"
    ) is not None:

        stats.append(
            f"<b>Requests:</b> "
            f"{esc(node.get('numRequests'))}"
        )

    if node.get(
        "numTiles"
    ) is not None:

        stats.append(
            f"<b>Tiles:</b> "
            f"{esc(node.get('numTiles'))}"
        )

    stats_html = ""

    if stats:

        stats_html = (
            '<div class="stats">'
            + " &nbsp; ".join(stats)
            + "</div>"
        )

    # --------------------------------------------------------
    # Description
    # --------------------------------------------------------

    description_html = ""

    if description:

        description_html = f"""
        <div class="description">
            {esc(description)}
        </div>
        """

    # --------------------------------------------------------
    # Blueprint string
    # --------------------------------------------------------

    blueprint_html = ""

    blueprint_text = asset.get(
        "blueprint_text"
    )

    if blueprint_text:

        blueprint_html = f"""
        <details class="blueprint-string">
            <summary>
                Show blueprint string
            </summary>

            <textarea
                readonly
            >{esc(blueprint_text)}</textarea>
        </details>
        """

    # --------------------------------------------------------
    # Children
    # --------------------------------------------------------

    children_html = ""

    children = node.get(
        "children"
    )

    if isinstance(
        children,
        list
    ):

        children_html = "\n".join(
            render_node(
                child,
                assets,
                level + 1
            )
            for child in children
        )

    return f"""
    <section
        class="node level-{level}"
        id="node-{esc(index)}"
    >

        <div class="node-header">

            <div>

                <div class="node-type">
                    {node_type}
                </div>

                <h2>
                    {esc(name)}
                </h2>

            </div>

            <div class="node-index">
                #{esc(index)}
            </div>

        </div>

        {image_html}

        {description_html}

        {stats_html}

        {blueprint_html}

        <div class="children">
            {children_html}
        </div>

    </section>
    """


# ============================================================
# HTML generation
# ============================================================

def make_html(
    post,
    nodes,
    assets
):

    if not isinstance(post, dict):
        post = {}

    post_info = (
        post.get("post")
        or {}
    )

    if not isinstance(
        post_info,
        dict
    ):
        post_info = {}

    title = (
        post_info.get("title")
        or post_info.get("id")
        or "FactorioBin Backup"
    )

    # IMPORTANT:
    # postedBy can explicitly be null.
    posted_by = (
        post_info.get("postedBy")
        or {}
    )

    if not isinstance(
        posted_by,
        dict
    ):
        posted_by = {}

    username = posted_by.get(
        "username"
    )

    created = post_info.get(
        "createdAt"
    )

    expires = post_info.get(
        "expiresAt"
    )

    roots = build_tree(
        nodes
    )

    body = "\n".join(
        render_node(
            node,
            assets
        )
        for node in roots
    )

    if not body:

        body = """
        <div class="warning">
            No valid nodes were found in
            this FactorioBin post.
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>
    {esc(title)} — FactorioBin Backup
</title>

<style>

* {{
    box-sizing: border-box;
}}

body {{
    margin: 0;
    background: #111318;
    color: #e8e8e8;
    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
}}

.container {{
    max-width: 1200px;
    margin: auto;
    padding: 32px;
}}

header {{
    margin-bottom: 32px;
    padding-bottom: 24px;
    border-bottom: 1px solid #333842;
}}

h1 {{
    margin: 0 0 12px 0;
    font-size: 32px;
}}

h2 {{
    margin: 6px 0 0;
    font-size: 24px;
}}

.meta {{
    color: #aeb4c0;
    line-height: 1.6;
}}

.node {{
    margin: 24px 0;
    padding: 24px;
    background: #191c22;
    border: 1px solid #30343d;
    border-radius: 12px;
}}

.node.level-1 {{
    margin-left: 24px;
}}

.node.level-2 {{
    margin-left: 48px;
}}

.node.level-3 {{
    margin-left: 72px;
}}

.node.level-4 {{
    margin-left: 96px;
}}

.node-header {{
    display: flex;
    justify-content: space-between;
    gap: 20px;
    align-items: flex-start;
}}

.node-type {{
    color: #9da5b3;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: .08em;
}}

.node-index {{
    color: #737b89;
    font-family: monospace;
}}

.preview {{
    margin: 20px 0;
    padding: 12px;
    background: #0d0f12;
    border-radius: 8px;
    text-align: center;
}}

.preview img {{
    max-width: 100%;
    height: auto;
    border-radius: 5px;
}}

.description {{
    margin: 16px 0;
    padding: 14px;
    color: #c8cdd6;
    background: #14171c;
    border-radius: 8px;
    white-space: pre-wrap;
}}

.stats {{
    margin: 12px 0;
    color: #9fa7b4;
    font-size: 14px;
}}

.children {{
    margin-top: 20px;
}}

details {{
    margin-top: 16px;
}}

summary {{
    cursor: pointer;
    color: #b8c7ff;
}}

textarea {{
    display: block;
    width: 100%;
    min-height: 180px;
    margin-top: 10px;
    padding: 12px;
    resize: vertical;
    background: #0b0d10;
    color: #d8dde7;
    border: 1px solid #30343d;
    border-radius: 7px;
    font-family: monospace;
    font-size: 12px;
}}

.warning {{
    padding: 20px;
    background: #2a2020;
    border: 1px solid #6b4545;
    border-radius: 8px;
}}

footer {{
    margin-top: 50px;
    padding-top: 20px;
    border-top: 1px solid #333842;
    color: #737b89;
    font-size: 13px;
}}

</style>

</head>

<body>

<div class="container">

<header>

<h1>
    {esc(title)}
</h1>

<div class="meta">

<div>
    <b>FactorioBin post:</b>
    {esc(post_info.get("id"))}
</div>

<div>
    <b>Posted by:</b>
    {esc(username or "anonymous")}
</div>

<div>
    <b>Created:</b>
    {esc(created)}
</div>

<div>
    <b>Expires:</b>
    {esc(expires or "Never")}
</div>

<div>
    <b>Nodes:</b>
    {len(nodes)}
</div>

</div>

</header>

{body}

<footer>
    Offline backup generated from FactorioBin.
</footer>

</div>

</body>

</html>
"""


# ============================================================
# Backup one post
# ============================================================

def backup_post(
    url,
    output_dir
):

    post_id, requested_node = parse_post_url(
        url
    )

    print()
    print("=" * 70)
    print(f"Backing up: {url}")
    print(f"Post ID:    {post_id}")
    print("=" * 70)

    # --------------------------------------------------------
    # Root post
    # --------------------------------------------------------

    root = get_node_info(
        post_id
    )

    if not isinstance(
        root,
        dict
    ):
        raise ValueError(
            "Root info.json did not return "
            "an object"
        )

    post = (
        root.get("post")
        or {}
    )

    if not isinstance(
        post,
        dict
    ):
        post = {}

    title = safe_filename(
        post.get("title")
        or post_id
    )

    post_dir = (
        output_dir
        / f"{post_id} - {title}"
    )

    post_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    # --------------------------------------------------------
    # Save raw post info
    # --------------------------------------------------------

    (post_dir / "post.json").write_text(
        json.dumps(
            post,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    # Also save the complete root response.
    (post_dir / "root-info.json").write_text(
        json.dumps(
            root,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    # --------------------------------------------------------
    # Get all nodes
    # --------------------------------------------------------

    print()
    print("Reading book/design tree...")

    nodes = get_all_nodes(
        post_id
    )

    print(
        f"Found {len(nodes)} nodes"
    )

    if requested_node is not None:
        print(
            f"Requested node: "
            f"{requested_node}"
        )

    # --------------------------------------------------------
    # Download every node
    # --------------------------------------------------------

    assets = {}

    total = len(nodes)

    for number, data in enumerate(
        nodes,
        1
    ):

        node = (
            data.get("node")
            if isinstance(
                data,
                dict
            )
            else None
        )

        if not isinstance(
            node,
            dict
        ):
            print(
                f"[{number}/{total}] "
                f"Skipping malformed node"
            )
            continue

        index = node.get(
            "index"
        )

        if index is None:
            index = number - 1
            node["index"] = index

        name = safe_filename(
            node.get("name")
            or f"node-{index}"
        )

        print(
            f"[{number}/{total}] "
            f"{node.get('type', 'unknown')} "
            f"#{index}: {name}"
        )

        node_dir = (
            post_dir
            / "nodes"
            / f"{int(index):04d} - {name}"
        )

        node_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        # ----------------------------------------------------
        # Save info.json
        # ----------------------------------------------------

        try:

            (node_dir / "info.json").write_text(
                json.dumps(
                    data,
                    indent=2,
                    ensure_ascii=False
                ),
                encoding="utf-8"
            )

        except Exception as e:

            print(
                f"    ! Could not save "
                f"info.json: {e}"
            )

        # ----------------------------------------------------
        # Blueprint
        # ----------------------------------------------------

        blueprint_path = None
        blueprint_text = None

        try:

            blueprint_path = download_blueprint(
                node,
                node_dir
            )

            if blueprint_path:

                blueprint_text = (
                    blueprint_path
                    .read_text(
                        encoding="utf-8",
                        errors="replace"
                    )
                )

        except Exception as e:

            print(
                f"    ! Blueprint download failed: "
                f"{e}"
            )

        # ----------------------------------------------------
        # Render
        # ----------------------------------------------------

        render_path = None
        image_data = None

        try:

            (
                render_path,
                image_data
            ) = download_render(
                node,
                node_dir
            )

        except Exception as e:

            print(
                f"    ! Render download failed: "
                f"{e}"
            )

        # ----------------------------------------------------
        # Save asset information
        # ----------------------------------------------------

        assets[index] = {
            "blueprint_path":
                blueprint_path,

            "blueprint_text":
                blueprint_text,

            "render_path":
                render_path,

            "image_data":
                image_data,
        }

    # --------------------------------------------------------
    # Create HTML
    # --------------------------------------------------------

    print()
    print("Generating backup.html...")

    try:

        html_text = make_html(
            root,
            nodes,
            assets
        )

    except Exception as e:

        # Do NOT lose the backup because of
        # malformed metadata.

        print(
            f"    ! HTML generation error: "
            f"{e}"
        )

        html_text = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>FactorioBin Backup - {esc(post_id)}</title>
<style>
body {{
    font-family: sans-serif;
    background: #111318;
    color: #eee;
    padding: 30px;
}}
pre {{
    white-space: pre-wrap;
}}
</style>
</head>
<body>
<h1>FactorioBin Backup - {esc(post_id)}</h1>
<p>
The backup data was downloaded, but the normal
HTML page could not be generated.
</p>
<h2>Error</h2>
<pre>{esc(e)}</pre>
</body>
</html>
"""

    html_path = (
        post_dir
        / "backup.html"
    )

    html_path.write_text(
        html_text,
        encoding="utf-8"
    )

    print()
    print("Backup complete:")
    print(
        f"  {html_path}"
    )

    return html_path


# ============================================================
# URL list handling
# ============================================================

def load_urls_from_file(path):

    urls = []

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"URL file not found: {path}"
        )

    for line in path.read_text(
        encoding="utf-8"
    ).splitlines():

        line = line.strip()

        # Empty lines
        if not line:
            continue

        # Comments
        if line.startswith("#"):
            continue

        urls.append(line)

    return urls


def unique_urls(urls):

    result = []

    seen = set()

    for url in urls:

        url = url.strip()

        if not url:
            continue

        if url in seen:
            continue

        seen.add(url)
        result.append(url)

    return result


# ============================================================
# Main
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Backup FactorioBin posts, including "
            "blueprints, books and render images."
        )
    )

    parser.add_argument(
        "urls",
        nargs="*",
        help=(
            "FactorioBin post URLs"
        )
    )

    parser.add_argument(
        "-f",
        "--file",
        help=(
            "Text file containing FactorioBin "
            "URLs, one per line"
        )
    )

    parser.add_argument(
        "-o",
        "--output",
        default="factorio-backups",
        help=(
            "Output directory "
            "(default: factorio-backups)"
        )
    )

    args = parser.parse_args()

    # --------------------------------------------------------
    # Collect URLs
    # --------------------------------------------------------

    urls = list(
        args.urls
    )

    if args.file:

        urls.extend(
            load_urls_from_file(
                args.file
            )
        )

    urls = unique_urls(
        urls
    )

    if not urls:

        parser.error(
            "Provide at least one FactorioBin URL "
            "or use --file posts.txt"
        )

    print(
        f"Found {len(urls)} unique URL(s)"
    )

    # --------------------------------------------------------
    # Output directory
    # --------------------------------------------------------

    output_dir = Path(
        args.output
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    # --------------------------------------------------------
    # Process everything
    # --------------------------------------------------------

    successful = 0
    failed = 0

    for url in urls:

        try:

            backup_post(
                url,
                output_dir
            )

            successful += 1

        except KeyboardInterrupt:

            print(
                "\nInterrupted."
            )

            sys.exit(130)

        except Exception as e:

            failed += 1

            print(
                f"\nERROR backing up "
                f"{url}: {e}",
                file=sys.stderr
            )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("BACKUP SUMMARY")
    print("=" * 70)
    print(
        f"Successful: {successful}"
    )
    print(
        f"Failed:     {failed}"
    )
    print(
        f"Output:     {output_dir.resolve()}"
    )
    print("=" * 70)


if __name__ == "__main__":
    main()
