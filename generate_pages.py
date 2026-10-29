import os
import sys
import json
import re
import html
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import shutil
import random
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from string import Template

# 1. Configuration & Hub Base URL Detection
sitemap_url = sys.argv[1] if len(sys.argv) > 1 else "https://stylesdays.com/sitemap_index.xml"

# Auto-detect GitHub Pages URL from environment or CLI arg 2
hub_base_url = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("HUB_BASE_URL", "")
if not hub_base_url:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo and "/" in repo:
        owner, repo_name = repo.split("/", 1)
        if repo_name.lower() == f"{owner.lower()}.github.io":
            hub_base_url = f"https://{owner.lower()}.github.io"
        else:
            hub_base_url = f"https://{owner.lower()}.github.io/{repo_name}"
    else:
        hub_base_url = "https://small-home-organization.github.io"

hub_base_url = hub_base_url.rstrip("/")
print(f"Fetching source sitemap from: {sitemap_url}")
print(f"Target Hub Base URL for SEO & OpenGraph: {hub_base_url}")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def fetch_data(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except Exception as e:
        print(f"Notice: Could not fetch {url}: {e}")
        return None

def parse_sitemap(url, found_urls=None):
    if found_urls is None:
        found_urls = set()
    xml_data = fetch_data(url)
    if not xml_data:
        return found_urls
    try:
        root = ET.fromstring(xml_data)
        namespace = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        sitemaps = root.findall('ns:sitemap', namespace)
        if sitemaps:
            for sm in sitemaps:
                loc = sm.find('ns:loc', namespace)
                if loc is not None and loc.text:
                    sub_url = loc.text.strip()
                    # Skip category/page sitemaps if we only want articles, or keep post sitemaps
                    if "category-sitemap" in sub_url or "page-sitemap" in sub_url or "author-sitemap" in sub_url:
                        continue
                    print(f"Found sub-sitemap: {sub_url}")
                    parse_sitemap(sub_url, found_urls)
        else:
            urls = root.findall('ns:url', namespace)
            for u in urls:
                loc = u.find('ns:loc', namespace)
                if loc is not None and loc.text:
                    found_urls.add(loc.text.strip())
    except Exception as e:
        print(f"Error parsing XML for {url}: {e}")
    return found_urls

# 2. Gather all post URLs from Sitemap
all_urls = parse_sitemap(sitemap_url)
print(f"Total unique post URLs found in sitemaps: {len(all_urls)}")

# 3. Fetch Live WordPress Data (Titles, Excerpts, Featured Images, Dates)
posts_data = {}
page = 1
per_page = 100

while True:
    print(f"Fetching WordPress REST API batch {page}...")
    wp_api_url = f"https://stylesdays.com/wp-json/wp/v2/posts?per_page={per_page}&page={page}&_embed=1"
    wp_data = fetch_data(wp_api_url)
    if not wp_data:
        break
    try:
        posts = json.loads(wp_data.decode('utf-8'))
        if not posts or not isinstance(posts, list) or len(posts) == 0:
            break
        for post in posts:
            slug = post.get('slug')
            if not slug:
                continue
            raw_title = post.get('title', {}).get('rendered', slug.replace('-', ' ').title())
            clean_title = html.unescape(re.sub('<[^<]+>', '', raw_title)).strip()

            raw_excerpt = post.get('excerpt', {}).get('rendered', '')
            clean_excerpt = html.unescape(re.sub('<[^<]+>', '', raw_excerpt)).strip()
            if not clean_excerpt:
                clean_excerpt = f"Explore our complete {clean_title} guide on stylesdays.com. This curated feature breaks down practical room styling ideas and layouts."

            pub_date = post.get('date_gmt') or post.get('date') or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if not pub_date.endswith("Z") and "+" not in pub_date:
                pub_date += "Z"

            mod_date = post.get('modified_gmt') or post.get('modified') or pub_date
            if not mod_date.endswith("Z") and "+" not in mod_date:
                mod_date += "Z"

            image_url = ""
            try:
                embedded = post.get('_embedded', {})
                featured_media = embedded.get('wp:featuredmedia', [{}])[0]
                if isinstance(featured_media, dict) and 'source_url' in featured_media:
                    image_url = featured_media['source_url']
            except Exception:
                pass

            posts_data[slug] = {
                'slug': slug,
                'title': clean_title,
                'description': clean_excerpt,
                'remote_image': image_url,
                'canonical_url': f"https://stylesdays.com/{slug}/",
                'pub_date': pub_date,
                'mod_date': mod_date
            }
        page += 1
    except Exception as e:
        print(f"Finished or encountered error on WP API page {page}: {e}")
        break

# Ensure any URL present in sitemap but missed in WP API batch is also included
for url in all_urls:
    clean_url = url if url.endswith('/') else url + '/'
    parts = [p for p in clean_url.split('/') if p]
    if len(parts) <= 2:
        continue
    slug = parts[-1]
    if slug in ('category', 'tag', 'author', 'page') or 'stylesdays.com' not in clean_url:
        continue
    if slug not in posts_data:
        human_title = slug.replace('-', ' ').title()
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        posts_data[slug] = {
            'slug': slug,
            'title': human_title,
            'description': f"Explore our complete {human_title} guide on stylesdays.com. Discover practical room styling ideas, layouts, and DIY tips.",
            'remote_image': "",
            'canonical_url': clean_url,
            'pub_date': now_iso,
            'mod_date': now_iso
        }

print(f"Total posts prepared for generation: {len(posts_data)}")

# 4. Download Featured Images Locally for Fast Self-Hosted Delivery & Pinterest Rich Pins
IMAGES_DIR = os.path.join("assets", "images")
os.makedirs(IMAGES_DIR, exist_ok=True)

# Generate a clean fallback SVG/JPG placeholder in assets/images/default.jpg if needed
DEFAULT_REMOTE_IMG = "https://images.unsplash.com/photo-1616594039964-ae9021a400a0?auto=format&fit=crop&w=1200&q=85"
DEFAULT_LOCAL_REL = "/assets/images/default-hero.jpg"
default_local_file = os.path.join(IMAGES_DIR, "default-hero.jpg")

if not os.path.exists(default_local_file):
    img_bytes = fetch_data(DEFAULT_REMOTE_IMG, timeout=15)
    if img_bytes:
        with open(default_local_file, "wb") as f:
            f.write(img_bytes)

def get_image_extension(url):
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    for ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif'):
        if path.endswith(ext):
            return ext
    return '.jpg'

def download_post_image(slug, info):
    remote_url = info.get('remote_image', '').strip()
    if not remote_url:
        info['local_image_rel'] = DEFAULT_LOCAL_REL
        info['local_image_abs'] = f"{hub_base_url}{DEFAULT_LOCAL_REL}"
        return slug, False

    ext = get_image_extension(remote_url)
    filename = f"{slug}{ext}"
    local_path = os.path.join(IMAGES_DIR, filename)
    rel_url = f"/assets/images/{filename}"

    # Reuse existing image if already downloaded and non-empty
    if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
        info['local_image_rel'] = rel_url
        info['local_image_abs'] = f"{hub_base_url}{rel_url}"
        return slug, True

    req = urllib.request.Request(remote_url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            if data and len(data) > 512:
                with open(local_path, "wb") as f:
                    f.write(data)
                info['local_image_rel'] = rel_url
                info['local_image_abs'] = f"{hub_base_url}{rel_url}"
                return slug, True
    except Exception:
        pass

    # Fallback if download fails
    info['local_image_rel'] = DEFAULT_LOCAL_REL
    info['local_image_abs'] = f"{hub_base_url}{DEFAULT_LOCAL_REL}"
    return slug, False

print("Downloading featured images locally using parallel workers (for fast GitHub Pages & Pinterestbot crawl)...")
downloaded_count = 0
with ThreadPoolExecutor(max_workers=16) as executor:
    futures = [executor.submit(download_post_image, slug, data) for slug, data in posts_data.items()]
    for future in as_completed(futures):
        _, success = future.result()
        if success:
            downloaded_count += 1

print(f"Locally cached {downloaded_count}/{len(posts_data)} featured images in /assets/images/.")

# 5. HTML Template with Full Pinterest Rich Pin Meta + Schema.org Article JSON-LD + Local Images
HIGH_CONVERTING_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>$title_attr | StylesDays Editorial</title>
  <meta name="description" content="$desc_attr">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">

  <!-- Canonical & Pinterest Rich Pin / OpenGraph Meta Tags -->
  <link rel="canonical" href="$hub_page_url" />
  <meta property="og:type" content="article">
  <meta property="og:title" content="$title_attr">
  <meta property="og:description" content="$desc_attr">
  <meta property="og:image" content="$image_abs">
  <meta property="og:image:alt" content="$title_attr">
  <meta property="og:url" content="$hub_page_url">
  <meta property="og:site_name" content="StylesDays Editorial">
  <meta property="article:published_time" content="$pub_date">
  <meta property="article:modified_time" content="$mod_date">
  <meta property="article:section" content="$category">
  <meta property="article:author" content="StylesDays Editorial Team">

  <!-- Twitter / X Card Metadata -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="$title_attr">
  <meta name="twitter:description" content="$desc_attr">
  <meta name="twitter:image" content="$image_abs">

  <!-- Preload Local Hero Image for Instant LCP -->
  <link rel="preload" as="image" href="$image_rel">

  <!-- Schema.org JSON-LD for Pinterest Article Rich Pins & Google Search -->
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": $json_title,
    "description": $json_desc,
    "image": [$json_image_abs],
    "datePublished": "$pub_date",
    "dateModified": "$mod_date",
    "author": {
      "@type": "Organization",
      "name": "StylesDays Editorial",
      "url": "https://stylesdays.com"
    },
    "publisher": {
      "@type": "Organization",
      "name": "StylesDays",
      "url": "https://stylesdays.com"
    },
    "mainEntityOfPage": {
      "@type": "WebPage",
      "@id": "$hub_page_url"
    }
  }
  </script>

  <script src="https://cdn.tailwindcss.com"></script>

  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Playfair+Display:ital,wght@0,600;0,700;1,400&display=swap" rel="stylesheet">

  <style>
    body { font-family: 'Inter', sans-serif; background-color: #f9fafb; color: #111827; -webkit-font-smoothing: antialiased; }
    .font-serif-title { font-family: 'Playfair Display', Georgia, serif; }
    @keyframes subtlePulse {
      0%, 100% { transform: scale(1); box-shadow: 0 10px 25px -5px rgba(230, 0, 35, 0.4); }
      50% { transform: scale(1.02); box-shadow: 0 18px 30px -5px rgba(230, 0, 35, 0.6); }
    }
    .pinterest-cta-pulse { animation: subtlePulse 2.5s ease-in-out infinite; }
    .pinterest-cta-pulse:hover { animation: none; transform: scale(1.02); }
  </style>
</head>
<body class="min-h-screen flex flex-col selection:bg-[#E60023] selection:text-white">

  <!-- Clean Minimal Header -->
  <header class="w-full bg-white/90 backdrop-blur-md border-b border-gray-200 sticky top-0 z-40 shadow-sm">
    <div class="max-w-3xl mx-auto px-5 py-3 flex items-center justify-between">
      <a href="/" class="font-serif-title text-xl font-bold tracking-tight text-gray-900 hover:text-[#E60023] transition">StylesDays</a>
      <div class="text-[10px] font-bold uppercase tracking-wider text-gray-400 flex items-center gap-1.5">
        <span class="w-2 h-2 rounded-full bg-green-500"></span> Official Curated Hub
      </div>
    </div>
  </header>

  <main class="max-w-3xl w-full mx-auto px-5 py-8 flex-grow">

    <!-- Local Self-Hosted Hero Image -->
    <a href="$canonical_url" target="_blank" rel="noopener" class="block relative w-full aspect-[16/10] sm:aspect-[16/9] rounded-2xl overflow-hidden shadow-sm mb-6 group bg-gray-100">
      <img src="$image_rel" alt="$title_attr" fetchpriority="high" decoding="async" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105">
      <div class="absolute top-4 left-4 bg-white/95 backdrop-blur-md text-gray-900 text-xs font-bold uppercase tracking-wider px-3 py-1.5 rounded-full shadow-sm">
        $category
      </div>
    </a>

    <!-- Title & Intro -->
    <div class="mb-8">
      <h1 class="font-serif-title text-3xl sm:text-4xl font-bold text-gray-900 leading-tight mb-4 hover:text-[#E60023] transition">
        <a href="$canonical_url" target="_blank" rel="noopener">$title</a>
      </h1>
      <p class="text-gray-600 text-base sm:text-lg leading-relaxed">
        $description
      </p>
    </div>

    <!-- MAIN CONVERSION ZONE -->
    <div class="bg-white rounded-3xl shadow-sm border border-gray-200 p-6 sm:p-8 mb-10 text-center">
      <p class="text-xs font-bold uppercase tracking-wider text-gray-400 mb-5">Read the full illustrated guide</p>

      <a href="$canonical_url" target="_blank" rel="noopener" class="pinterest-cta-pulse w-full sm:w-auto inline-flex items-center justify-center gap-2 bg-[#E60023] text-white text-lg font-bold py-4 px-10 rounded-2xl shadow-lg transition duration-150">
        <span>Continue to StylesDays</span>
        <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M14 5l7 7m0 0l-7 7m7-7H3"></path>
        </svg>
      </a>

      <div class="flex flex-wrap items-center justify-center gap-5 mt-6 text-xs text-gray-500 font-medium">
        <div class="flex items-center gap-1.5">
          <svg class="w-4 h-4 text-green-500" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path></svg>
          <span>Safe Official Link</span>
        </div>
        <div class="flex items-center gap-1.5">
          <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
          <span>Instant Access</span>
        </div>
      </div>
    </div>

    <!-- Substantive Editorial Value Cards -->
    <div class="grid sm:grid-cols-2 gap-4 mb-12">
      <div class="bg-gray-100/50 rounded-2xl p-5 border border-gray-200/50">
        <h2 class="font-bold text-sm text-gray-800 mb-3">Key Highlights</h2>
        <ul class="text-sm text-gray-600 space-y-2">
          <li class="flex items-start gap-2"><span class="text-gray-400 mt-0.5">•</span> Intentional styling for real-life spaces.</li>
          <li class="flex items-start gap-2"><span class="text-gray-400 mt-0.5">•</span> Layered textures &amp; optimal lighting tips.</li>
        </ul>
      </div>
      <div class="bg-gray-100/50 rounded-2xl p-5 border border-gray-200/50">
        <h2 class="font-bold text-sm text-gray-800 mb-3">Project Prep</h2>
        <ul class="text-sm text-gray-600 space-y-2">
          <li class="flex items-start gap-2"><input type="checkbox" checked disabled class="mt-1 rounded text-gray-400"> Explore full room photos on StylesDays</li>
          <li class="flex items-start gap-2"><input type="checkbox" checked disabled class="mt-1 rounded text-gray-400"> Save inspiration to your Pinterest board</li>
        </ul>
      </div>
    </div>

    <!-- Related Posts (Using Local Downloaded Images & Direct Links to StylesDays) -->
    <div class="pt-8 border-t border-gray-200">
      <h2 class="font-serif-title text-2xl font-bold text-gray-900 mb-6">More Featured Guides</h2>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-5">
        $related_posts_html
      </div>
    </div>

  </main>

  <footer class="w-full border-t border-gray-200 bg-white py-6 mt-auto text-center text-xs text-gray-400">
    <p>&copy; 2026 StylesDays Editorial Hub. <a href="/sitemap.xml" class="underline hover:text-gray-600 ml-2">Sitemap</a></p>
  </footer>
</body>
</html>
""")

def get_related_posts_html(current_slug, posts_data):
    available_slugs = [s for s in posts_data.keys() if s != current_slug]
    if not available_slugs:
        return ""
    selected_slugs = random.sample(available_slugs, min(3, len(available_slugs)))
    cards_html = ""
    for s in selected_slugs:
        p = posts_data[s]
        img_rel = p.get('local_image_rel', DEFAULT_LOCAL_REL)
        title = html.escape(p.get('title', 'StylesDays Guide'), quote=True)
        url = p.get('canonical_url', f'https://stylesdays.com/{s}/')

        cat = "Home Decor"
        if "hack" in s: cat = "IKEA Hacks"
        elif "storage" in s or "pax" in s: cat = "IKEA Storage"
        elif "bedroom" in s: cat = "IKEA Bedroom"

        card = f"""
        <a href="{url}" target="_blank" rel="noopener" class="block bg-white rounded-2xl border border-gray-200 overflow-hidden shadow-sm hover:shadow-md transition group">
          <div class="aspect-[16/10] overflow-hidden relative bg-gray-100">
            <img src="{img_rel}" alt="{title}" loading="lazy" decoding="async" class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500">
            <span class="absolute top-2 left-2 bg-white/90 backdrop-blur-sm text-gray-900 text-[9px] font-bold uppercase px-2 py-1 rounded-full">
              {cat}
            </span>
          </div>
          <div class="p-4">
            <h3 class="font-serif-title text-sm font-bold text-gray-900 group-hover:text-[#E60023] transition-colors leading-snug line-clamp-2">
              {title}
            </h3>
          </div>
        </a>
        """
        cards_html += card
    return cards_html

# 6. Clean Old Generated Slug Directories & Build All Article Pages
RESERVED_DIRS = {'.git', '.github', 'assets', 'node_modules', '__pycache__'}
print(f"\nCleaning old slug folders and generating {len(posts_data)} SEO-optimized pages...")

for slug, data in posts_data.items():
    if slug in RESERVED_DIRS:
        continue

    category = "Home Decor"
    if "hack" in slug:
        category = "IKEA Hacks"
    elif "storage" in slug or "pax" in slug or "closet" in slug:
        category = "IKEA Storage"
    elif "daybed" in slug or "guest" in slug:
        category = "Guest Bedroom & Daybeds"
    elif "bedroom" in slug:
        category = "Bedroom Design"

    related_posts_html = get_related_posts_html(slug, posts_data)
    hub_page_url = f"{hub_base_url}/{slug}/"

    shutil.rmtree(slug, ignore_errors=True)
    os.makedirs(slug, exist_ok=True)
    file_path = os.path.join(slug, 'index.html')

    page_content = HIGH_CONVERTING_TEMPLATE.safe_substitute(
        title=html.escape(data['title']),
        title_attr=html.escape(data['title'], quote=True),
        description=html.escape(data['description']),
        desc_attr=html.escape(data['description'], quote=True),
        canonical_url=data['canonical_url'],
        hub_page_url=hub_page_url,
        image_rel=data.get('local_image_rel', DEFAULT_LOCAL_REL),
        image_abs=data.get('local_image_abs', f"{hub_base_url}{DEFAULT_LOCAL_REL}"),
        pub_date=data['pub_date'],
        mod_date=data['mod_date'],
        category=category,
        json_title=json.dumps(data['title']),
        json_desc=json.dumps(data['description']),
        json_image_abs=json.dumps(data.get('local_image_abs', f"{hub_base_url}{DEFAULT_LOCAL_REL}")),
        related_posts_html=related_posts_html
    )

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(page_content)

# 7. Generate Root Homepage (index.html) for Crawl Discovery & Brand Authority
print("Generating root index.html catalog page...")
featured_home_slugs = list(posts_data.keys())[:24]
home_cards_html = ""
for s in featured_home_slugs:
    p = posts_data[s]
    img_rel = p.get('local_image_rel', DEFAULT_LOCAL_REL)
    t_esc = html.escape(p['title'], quote=True)
    d_esc = html.escape(p['description'])
    home_cards_html += f"""
    <a href="/{s}/" class="block bg-white rounded-2xl border border-gray-200 overflow-hidden shadow-sm hover:shadow-md transition group flex flex-col">
      <div class="aspect-[16/10] bg-gray-100 overflow-hidden relative">
        <img src="{img_rel}" alt="{t_esc}" loading="lazy" class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500">
      </div>
      <div class="p-5 flex-grow flex flex-col justify-between">
        <div>
          <h2 class="font-serif-title text-lg font-bold text-gray-900 group-hover:text-[#E60023] transition mb-2 line-clamp-2">{t_esc}</h2>
          <p class="text-xs text-gray-500 line-clamp-2 mb-4">{d_esc}</p>
        </div>
        <span class="text-xs font-bold text-[#E60023]">Explore Guide &rarr;</span>
      </div>
    </a>
    """

HOME_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>StylesDays Editorial Hub | Home Decor, Bedroom Styling &amp; IKEA Hacks</title>
  <meta name="description" content="Official curated Home Decor, Bedroom Styling, and IKEA Hacks directory by StylesDays.">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <link rel="canonical" href="{hub_base_url}/" />
  <meta property="og:type" content="website">
  <meta property="og:title" content="StylesDays Editorial Hub">
  <meta property="og:description" content="Curated Home Decor, Bedroom Styling, and IKEA Hacks guides.">
  <meta property="og:url" content="{hub_base_url}/">
  <meta property="og:image" content="{hub_base_url}{DEFAULT_LOCAL_REL}">
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Playfair+Display:wght@600;700&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Inter', sans-serif; background-color: #f9fafb; color: #111827; }}
    .font-serif-title {{ font-family: 'Playfair Display', Georgia, serif; }}
  </style>
</head>
<body class="min-h-screen flex flex-col">
  <header class="w-full bg-white border-b border-gray-200 sticky top-0 z-40">
    <div class="max-w-5xl mx-auto px-5 py-4 flex items-center justify-between">
      <span class="font-serif-title text-2xl font-bold text-gray-900">StylesDays</span>
      <a href="https://stylesdays.com" target="_blank" rel="noopener" class="text-xs font-bold text-[#E60023] hover:underline">Visit Official Site &rarr;</a>
    </div>
  </header>
  <main class="max-w-5xl w-full mx-auto px-5 py-10 flex-grow">
    <div class="mb-8">
      <h1 class="font-serif-title text-3xl sm:text-4xl font-bold text-gray-900 mb-2">Curated Home Decor &amp; Styling Guides</h1>
      <p class="text-gray-600 text-sm sm:text-base">Browse our latest room inspiration, smart storage solutions, and DIY furniture upgrades.</p>
    </div>
    <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-6">
      {home_cards_html}
    </div>
  </main>
  <footer class="w-full border-t border-gray-200 bg-white py-6 mt-12 text-center text-xs text-gray-400">
    <p>&copy; 2026 StylesDays Editorial Hub. <a href="/sitemap.xml" class="underline ml-2">XML Sitemap</a></p>
  </footer>
</body>
</html>
"""
with open("index.html", "w", encoding="utf-8") as f:
    f.write(HOME_HTML)

# 8. Generate Pinterest-Optimized XML Sitemap with Image Extensions (sitemap.xml)
print("Generating sitemap.xml with Google & Pinterest Image Sitemap extensions...")
now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
sitemap_entries = [
    f"""  <url>
    <loc>{hub_base_url}/</loc>
    <lastmod>{now_iso}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>"""
]

for slug, data in posts_data.items():
    if slug in RESERVED_DIRS:
        continue
    page_loc = html.escape(f"{hub_base_url}/{slug}/")
    img_loc = html.escape(data.get('local_image_abs', f"{hub_base_url}{DEFAULT_LOCAL_REL}"))
    img_title = html.escape(data['title'])
    lastmod = data.get('mod_date', now_iso)

    sitemap_entries.append(f"""  <url>
    <loc>{page_loc}</loc>
    <lastmod>{lastmod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
    <image:image>
      <image:loc>{img_loc}</image:loc>
      <image:title>{img_title}</image:title>
    </image:image>
  </url>""")

sitemap_xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
{chr(10).join(sitemap_entries)}
</urlset>
"""
with open("sitemap.xml", "w", encoding="utf-8") as f:
    f.write(sitemap_xml_content)

# 9. Generate robots.txt Explicitly Allowing Pinterestbot & Pointing to Sitemap
print("Generating robots.txt optimized for Pinterestbot & search crawlers...")
robots_txt_content = f"""User-agent: Pinterestbot
Allow: /

User-agent: Pinterest
Allow: /

User-agent: Googlebot
Allow: /

User-agent: Googlebot-Image
Allow: /

User-agent: *
Allow: /

Sitemap: {hub_base_url}/sitemap.xml
"""
with open("robots.txt", "w", encoding="utf-8") as f:
    f.write(robots_txt_content)

# 10. Generate .nojekyll so GitHub Pages Serves All Assets & Folders Without Jekyll Filtering
with open(".nojekyll", "w", encoding="utf-8") as f:
    f.write("")

print("\nAll pages, local images, sitemap.xml, robots.txt, and .nojekyll successfully generated!")
