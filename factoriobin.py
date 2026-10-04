#!/usr/bin/env python3

import argparse
import base64
import html
import json
import mimetypes
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests


SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "FactorioBin-Backup/1.0"
})

TIMEOUT = 30


# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

def get_json(url):
    r = SESSION.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_bytes(url):
    r = SESSION.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "")


# ------------------------------------------------------------
# FactorioBin URL handling
# ------------------------------------------------------------

def parse_post_url(url):
    """
    Supports:

        https://factoriobin.com/post/demo
        https://factoriobin.com/post/demo/23
        https://factoriobin.com/post/demo/23/
    """

    parsed = urlparse(url)

    if parsed.netloc not in ("factoriobin.com", "www.factoriobin.com"):
        raise ValueError(f"Not a FactorioBin URL: {url}")

    parts = [x for x in parsed.path.split("/") if x]

    if len(parts) < 2 or parts[0] != "post":
        raise ValueError(f"Expected /post/<id>[/<node>]: {url}")

    post_id = parts[1]

    node_index = None
    if len(parts) >= 3:
        try:
            node_index = int(parts[2])
        except ValueError:
            raise ValueError(f"Invalid node index: {parts[2]}")

    return post_id, node_index


# ------------------------------------------------------------
# info.json
# ------------------------------------------------------------

def get_node_info(post_id, node_index=None):
    if node_index is None:
        url = f"https://factoriobin.com/post/{post_id}/info.json"
    else:
        url = f"https://factoriobin.com/post/{post_id}/{node_index}/info.json"

    return get_json(url)


def get_all_nodes(post_id):
    """
    FactorioBin's book pages expose nodes by their numeric index.

    We first get node 0, then follow indices until FactorioBin
    stops returning nodes.
    """

    nodes = []

    index = 0

    while True:
        try:
            data = get_node_info(post_id, index)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                break
            raise

        node = data.get("node")

        if not node:
            break

        nodes.append(data)

        index += 1

    return nodes


# ------------------------------------------------------------
# File helpers
# ------------------------------------------------------------

def safe_filename(name):
    name = name or "Untitled"

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name[:180] or "Untitled"


def data_uri(data, content_type):
    if not content_type:
        content_type = "image/jpeg"

    if ";" in content_type:
        content_type = content_type.split(";", 1)[0]

    encoded = base64.b64encode(data).decode("ascii")

    return f"data:{content_type};base64,{encoded}"


# ------------------------------------------------------------
# Blueprint string
# ------------------------------------------------------------

def download_blueprint(node, directory):
    url = node.get("blueprintStringUrl")

    if not url:
        return None

    data, _ = get_bytes(url)

    path = directory / "blueprint.txt"
    path.write_bytes(data)

    return path


# ------------------------------------------------------------
# Render image
# ------------------------------------------------------------

def download_render(node, directory):
    url = node.get("renderImageUrl")

    if not url:
        return None, None

    try:
        data, content_type = get_bytes(url)
    except requests.HTTPError:
        print(f"    ! Render unavailable: {url}")
        return None, None

    extension = ".jpg"

    if content_type:
        guessed = mimetypes.guess_extension(
            content_type.split(";", 1)[0]
        )
        if guessed:
            extension = guessed

    path = directory / f"render{extension}"
    path.write_bytes(data)

    return path, data_uri(data, content_type)


# ------------------------------------------------------------
# Node tree
# ------------------------------------------------------------

def build_tree(nodes):
    """
    Converts FactorioBin's flat node list:

        index
        parentIndex

    into a nested tree.
    """

    by_index = {}

    for data in nodes:
        node = data["node"]
        by_index[node["index"]] = node

    children = {}

    for node in by_index.values():
        parent = node.get("parentIndex")

        if parent is None:
            continue

        children.setdefault(parent, []).append(node)

    def make_node(node):
        result = dict(node)

        result["children"] = [
            make_node(child)
            for child in sorted(
                children.get(node["index"], []),
                key=lambda x: x["index"]
            )
        ]

        return result

    roots = [
        node
        for node in by_index.values()
        if node.get("parentIndex") is None
    ]

    return [
        make_node(root)
        for root in sorted(roots, key=lambda x: x["index"])
    ]


# ------------------------------------------------------------
# HTML
# ------------------------------------------------------------

def esc(value):
    if value is None:
        return ""

    return html.escape(str(value))


def node_type_label(node):
    return {
        "blueprint-book": "📚 Blueprint Book",
        "blueprint": "🔷 Blueprint",
        "upgrade-planner": "⬆️ Upgrade Planner",
        "deconstruction-planner": "🧹 Deconstruction Planner",
    }.get(
        node.get("type"),
        node.get("type", "Unknown")
    )


def render_node(node, assets, level=0):
    index = node["index"]

    asset = assets.get(index, {})

    name = node.get("name") or "Untitled"

    description = node.get("description")

    node_type = node_type_label(node)

    image_html = ""

    if asset.get("image_data"):
        image_html = f"""
        <div class="preview">
            <img src="{asset['image_data']}"
                 alt="{esc(name)} render">
        </div>
        """

    stats = []

    if node.get("factorioVersion"):
        stats.append(
            f"<b>Factorio:</b> {esc(node['factorioVersion'])}"
        )

    if node.get("numEntities") is not None:
        stats.append(
            f"<b>Entities:</b> {node['numEntities']}"
        )

    if node.get("numRequests") is not None:
        stats.append(
            f"<b>Requests:</b> {node['numRequests']}"
        )

    if node.get("numTiles") is not None:
        stats.append(
            f"<b>Tiles:</b> {node['numTiles']}"
        )

    stats_html = ""

    if stats:
        stats_html = (
            '<div class="stats">'
            + " &nbsp; ".join(stats)
            + "</div>"
        )

    description_html = ""

    if description:
        description_html = f"""
        <div class="description">
            {esc(description)}
        </div>
        """

    blueprint_html = ""

    if asset.get("blueprint_text"):
        blueprint_html = f"""
        <details class="blueprint-string">
            <summary>Show blueprint string</summary>
            <textarea readonly>{esc(asset['blueprint_text'])}</textarea>
        </details>
        """

    children_html = ""

    if node.get("children"):
        children_html = "\n".join(
            render_node(child, assets, level + 1)
            for child in node["children"]
        )

    return f"""
    <section class="node level-{level}" id="node-{index}">

        <div class="node-header">

            <div>
                <div class="node-type">{node_type}</div>

                <h2>
                    {esc(name)}
                </h2>
            </div>

            <div class="node-index">
                #{index}
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


def make_html(post, nodes, assets):
    post_info = post["post"]

    title = post_info.get("title") or post_info.get("id")

    username = (
        post_info.get("postedBy", {})
        .get("username")
    )

    created = post_info.get("createdAt")
    expires = post_info.get("expiresAt")

    roots = build_tree(nodes)

    body = "\n".join(
        render_node(node, assets)
        for node in roots
    )

    return f"""<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="utf-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>{esc(title)} — FactorioBin Backup</title>

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

    <h1>{esc(title)}</h1>

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


# ------------------------------------------------------------
# Backup
# ------------------------------------------------------------

def backup_post(url, output_dir):
    post_id, requested_node = parse_post_url(url)

    print(f"\n=== {post_id} ===")

    # Get root post information.
    root = get_node_info(post_id)

    post = root["post"]

    title = safe_filename(
        post.get("title") or post_id
    )

    post_dir = output_dir / f"{post_id} - {title}"

    post_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    # Save raw post info.
    (post_dir / "post.json").write_text(
        json.dumps(post, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Retrieve all nodes.
    print("Reading book/design tree...")

    nodes = get_all_nodes(post_id)

    print(f"Found {len(nodes)} nodes")

    # If URL points to a particular node, still back up
    # the complete post, but mark the requested node.
    if requested_node is not None:
        print(f"Requested node: {requested_node}")

    assets = {}

    for number, data in enumerate(nodes, 1):

        node = data["node"]

        index = node["index"]

        name = safe_filename(
            node.get("name") or f"node-{index}"
        )

        print(
            f"[{number}/{len(nodes)}] "
            f"{node.get('type')} "
            f"#{index}: {name}"
        )

        node_dir = (
            post_dir
            / "nodes"
            / f"{index:04d} - {name}"
        )

        node_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        # Save info.json.
        (node_dir / "info.json").write_text(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )

        # Blueprint string.
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
                f"    ! Blueprint download failed: {e}"
            )

        # Render.
        render_path = None
        image_data = None

        try:
            render_path, image_data = download_render(
                node,
                node_dir
            )

        except Exception as e:
            print(
                f"    ! Render download failed: {e}"
            )

        assets[index] = {
            "blueprint_path": blueprint_path,
            "blueprint_text": blueprint_text,
            "render_path": render_path,
            "image_data": image_data,
        }

    # Create HTML.
    html_text = make_html(
        root,
        nodes,
        assets
    )

    html_path = post_dir / "backup.html"

    html_path.write_text(
        html_text,
        encoding="utf-8"
    )

    print()
    print(f"Backup complete:")
    print(f"  {html_path}")

    return html_path


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Backup FactorioBin posts, including "
            "blueprints, books and render images."
        )
    )

    parser.add_argument(
        "urls",
        nargs="+",
        help="FactorioBin post URLs"
    )

    parser.add_argument(
        "-o",
        "--output",
        default="factorio-backups",
        help="Output directory"
    )

    args = parser.parse_args()

    output_dir = Path(args.output)

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    for url in args.urls:

        try:
            backup_post(
                url,
                output_dir
            )

        except Exception as e:

            print(
                f"\nERROR backing up {url}: {e}",
                file=sys.stderr
            )


if __name__ == "__main__":
    main()