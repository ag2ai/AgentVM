# Copyright (c) 2023 - 2025, AG2ai, Inc., AG2ai open-source projects maintainers and core contributors
#
# SPDX-License-Identifier: Apache-2.0
#
# Portions derived from  https://github.com/microsoft/autogen are under the MIT License.
# SPDX-License-Identifier: MIT
import mimetypes
import os
import pathlib
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union, Iterable
from urllib.parse import unquote, urljoin, urlparse

import pathvalidate
import requests
from serpapi.google_search import GoogleSearch
 
from .web_utils.cookies import COOKIES
from .mdconvert import FileConversionException, MarkdownConverter, UnsupportedFormatException

from bs4 import BeautifulSoup, Tag, NavigableString

# Default allow‑list ----------------------------------------------------------
DEFAULT_INCLUDE_ATTRS: List[str] = [
    "href",                #  ← added
    "title",
    "type",
    "name",
    "role",
    "aria-label",
    "placeholder",
    "value",
    "alt",
    "aria-expanded",
    "data-date-format",
]

def clean_markup(
    html: str | bytes | None,
    include_attributes: Iterable[str] = DEFAULT_INCLUDE_ATTRS,
    *,
    strip_tags: Tuple[str, ...] = ("script",),
) -> str:
    """
    1. Removes every attribute *not* listed in *include_attributes*  
       (defaults to the project‑wide DEFAULT_INCLUDE_ATTRS above).
    2. Drops every element whose tag name is in *strip_tags* (default: ("script",)).
    3. Prunes elements that end up empty (no attributes, no text, no child tags).

    The function is “safe”: it returns ``""`` for None / empty input and never
    raises on malformed HTML fragments.
    """
    if not html:
        return ""

    allowed = {a.lower() for a in include_attributes}
    STRIP_TAGS = {t.lower() for t in strip_tags}

    soup = BeautifulSoup(html, "html.parser")

    # -- 1. Remove entire tags we never want ----------------------------------
    for tag in soup.find_all(STRIP_TAGS):
        tag.decompose()

    # -- 2. Strip attributes not on the allow‑list ----------------------------
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower() not in allowed:
                del tag[attr]

    # -- 3. Prune elements that are now completely empty ----------------------
    def _is_empty(t: Tag) -> bool:
        if t.attrs:
            return False
        for c in t.contents:
            if isinstance(c, NavigableString) and c.strip():
                return False
            if isinstance(c, Tag):
                return False
        return True

    for tag in reversed(list(soup.find_all(True))):   # deepest‑first
        if _is_empty(tag):
            tag.decompose()

    return str(soup).strip() or ""

class SimpleWebBrowserEnv():
    """An extremely simple text-based web browser comparable to Lynx. Suitable for Agentic use."""

    def __init__(
        self,
        name: str = "SimpleWebBrowserEnv",
        description: str = "A simple text-based web browser environment.",
        start_page: Optional[str] = None,
        viewport_size: Optional[int] = 1024 * 8,
        cache_folder: Optional[Union[str, None]] = None,
        serpapi_key: Optional[Union[str, None]] = None,
        serper_key: Optional[Union[str, None]] = None,
        request_kwargs: Optional[Union[Dict[str, Any], None]] = {
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0"
            }
        },
    ):
        self._name = name
        self._description = description

        self.start_page: str = start_page if start_page else "about:blank"
        self.viewport_size = viewport_size  # Applies only to the standard uri types
        
        # cache_folder: for temporary browser cache (auto-downloaded files for viewing)
        self.cache_folder = cache_folder
        if self.cache_folder is None:
            # Default to a tmp directory in the current action folder
            self.cache_folder = os.path.join(os.path.dirname(__file__), "..", "tmp", "cache")
        if not os.path.exists(self.cache_folder):
            os.makedirs(self.cache_folder)
        self.history: List[Tuple[str, float]] = list()
        self.page_title: Optional[str] = None
        self.viewport_current_page = 0
        self.viewport_pages: List[Tuple[int, int]] = list()
        self.address_mapper: Dict[str, str] = {}  # Maps local file URIs to original web addresses
        self.serpapi_key = serpapi_key if serpapi_key else os.environ.get("SERPAPI_KEY")
        self.serper_key = serper_key if serper_key else os.environ.get("SERPER_KEY")
        self.request_kwargs = request_kwargs
        self.request_kwargs["cookies"] = COOKIES
        self._mdconvert = MarkdownConverter()
        self._page_content: str = ""
        self.set_address(self.start_page)

        self._find_on_page_query: Union[str, None] = None
        self._find_on_page_last_result: Union[int, None] = None  # Location of the last result

    def step(self, 
             action: str,
             url: Optional[str] = None,
             query: Optional[str] = None,
             path: Optional[str] = None,
             ) -> str:
        """Take a step in the environment."""
        result = ""
        is_success = True
        
        if action == "page_down":
            result, is_success = self.page_down()
        elif action == "page_up":
            result, is_success = self.page_up()
        elif action == "find_on_page":
            result, is_success = self.find_on_page(query)
        elif action == "find_next":
            result, is_success = self.find_next()
        elif action == "visit_page":
            result, is_success = self.visit_page(url)
        elif action == "download":
            result, is_success = self.download(url, path)
        elif action == "search":
            result, is_success = self.visit_page(f"google:{query}")
        else:
            raise ValueError(f"Unknown action: {action}")

        header, viewport = self.browser_state()
        finish_reason = "success" if is_success and "error 403" not in self.viewport.lower() and (self.page_title is None or "Error " not in self.page_title) else "error"
        
        return f"""
[Action Result]
{finish_reason}: {result}
[Web_Browser]
{header}
{viewport}
"""

    def reset(self) -> None:
        """Reset the environment."""
        self.history = []
        self.page_title = None
        self.viewport_current_page = 0
        self.viewport_pages = []
        self.address_mapper = {}
        self._page_content = ""
        self.set_address(self.start_page)
        self._find_on_page_query = None
        self._find_on_page_last_result = None
        self._mdconvert = MarkdownConverter()
        self._page_content = ""
        self.set_address(self.start_page)

    def close(self) -> None:
        """Close the environment."""
        self.reset()

    def browser_state(self) -> tuple[str, str]:
        header = f"Address: {self.address}\n"
        if self.page_title is not None:
            header += f"Title: {self.page_title}\n"

        current_page = self.viewport_current_page
        total_pages = len(self.viewport_pages)

        header += f"Viewport position: Showing page {current_page + 1} of {total_pages}.\n"
        return (header, self.viewport)
    
    @property
    def address(self) -> str:
        """Return the address of the current page."""
        current_address = self.history[-1][0]
        return self.get_browser_address(current_address)

    def get_browser_address(self, address: str) -> str:
        """Return the original web address if it exists, otherwise return the current address."""
        return self.address_mapper.get(address, address)

    def set_address(self, uri_or_path: str, filter_year: Optional[int] = None, update_only=False) -> None:
        # TODO: Handle anchors
        self.history.append((uri_or_path, time.time()))
        if update_only:
            # If we are just updating the address, don't fetch the page
            return

        # Handle special URIs
        if uri_or_path == "about:blank":
            self._set_page_content("")
        elif uri_or_path.startswith("google:"):
            self._serpapi_search(uri_or_path[len("google:") :].strip(), filter_year=filter_year)
        else:
            if (
                not uri_or_path.startswith("http:")
                and not uri_or_path.startswith("https:")
                and not uri_or_path.startswith("file:")
            ):
                if len(self.history) > 1:
                    prior_address = self.history[-2][0]
                    uri_or_path = urljoin(prior_address, uri_or_path)
                    # Update the address with the fully-qualified path
                    self.history[-1] = (uri_or_path, self.history[-1][1])
            self._fetch_page(uri_or_path)

        self.viewport_current_page = 0
        self.find_on_page_query = None
        self.find_on_page_viewport = None

    @property
    def viewport(self) -> str:
        """Return the content of the current viewport."""
        bounds = self.viewport_pages[self.viewport_current_page]
        return self.page_content[bounds[0] : bounds[1]]

    @property
    def page_content(self) -> str:
        """Return the full contents of the current page."""
        return self._page_content

    def _set_page_content(self, content: str) -> None:
        """Sets the text content of the current page."""
        self._page_content = content
        self._split_pages()
        if self.viewport_current_page >= len(self.viewport_pages):
            self.viewport_current_page = len(self.viewport_pages) - 1

    def page_down(self) -> Tuple[str, bool]:
        prev_page = self.viewport_current_page
        self.viewport_current_page = min(self.viewport_current_page + 1, len(self.viewport_pages) - 1)
        return f"Moved from page {prev_page + 1} to {self.viewport_current_page + 1}", True

    def page_up(self) -> Tuple[str, bool]:
        prev_page = self.viewport_current_page
        self.viewport_current_page = max(self.viewport_current_page - 1, 0)
        return f"Moved from page {prev_page + 1} to {self.viewport_current_page + 1}", True

    def find_on_page(self, query: str) -> Tuple[str, bool]:
        """Searches for the query from the current viewport forward, looping back to the start if necessary."""
        
        if query == "" or query is None:
            return "No query specified. Please provide a query to search for.", False

        # Did we get here via a previous find_on_page search with the same query?
        # If so, map to find_next
        if query == self._find_on_page_query and self.viewport_current_page == self._find_on_page_last_result:
            return self.find_next()

        # Ok it's a new search start from the current viewport
        self._find_on_page_query = query
        viewport_match = self._find_next_viewport(query, self.viewport_current_page)
        if viewport_match is None:
            self._find_on_page_last_result = None
            return "No matches found.", True
        else:
            self.viewport_current_page = viewport_match
            self._find_on_page_last_result = viewport_match
            # add some prefix to the viewport to show the match
            prefix = f"Query '{query}' match {self._find_full_count(query)} times on this url, showing the first match"
            return prefix, True

    def find_next(self) -> Tuple[str, bool]:
        """Scroll to the next viewport that matches the query"""

        if self._find_on_page_query is None:
            return "No query set yet. Use find_on_page first.", False

        starting_viewport = self._find_on_page_last_result
        if starting_viewport is None:
            starting_viewport = 0
        else:
            starting_viewport += 1
            if starting_viewport >= len(self.viewport_pages):
                starting_viewport = 0

        viewport_match = self._find_next_viewport(self._find_on_page_query, starting_viewport)
        if viewport_match is None:
            self._find_on_page_last_result = None
            return "No more matches found.", True
        else:
            self.viewport_current_page = viewport_match
            self._find_on_page_last_result = viewport_match
            prefix = f"Query '{self._find_on_page_query}' match {self._find_full_count(self._find_on_page_query)} times on this url, showing the next match"
            return prefix, True

    def _find_next_viewport(self, query: str, starting_viewport: int) -> Union[int, None]:
        """Search for matches between the starting viewport looping when reaching the end."""

        if query is None:
            return None

        # Normalize the query, and convert to a regular expression
        nquery = re.sub(r"\*", "__STAR__", query)
        nquery = " " + (" ".join(re.split(r"\W+", nquery))).strip() + " "
        nquery = nquery.replace(" __STAR__ ", "__STAR__ ")  # Merge isolated stars with prior word
        nquery = nquery.replace("__STAR__", ".*").lower()

        if nquery.strip() == "":
            return None

        idxs = list()
        idxs.extend(range(starting_viewport, len(self.viewport_pages)))
        idxs.extend(range(0, starting_viewport))

        for i in idxs:
            bounds = self.viewport_pages[i]
            content = self.page_content[bounds[0] : bounds[1]]

            # TODO: Remove markdown links and images
            ncontent = " " + (" ".join(re.split(r"\W+", content))).strip().lower() + " "
            if re.search(nquery, ncontent):
                return i

        return None

    def visit_page(self, path_or_uri: str, filter_year: Optional[int] = None) -> Tuple[str, bool]:
        """Update the address, visit the page, and return the content of the viewport."""
        if path_or_uri is None or path_or_uri == "":
            return "No URL specified. Please provide a URL to visit.", False
        
        try:
            self.set_address(path_or_uri, filter_year=filter_year)
            return f"Visited page: {path_or_uri}", True
        except Exception as e:
            return f"Error visiting page: {str(e)}", False

    def download(self, url: str, path: Optional[str] = None) -> Tuple[str, bool]:
        """Attempts to download content from the specified URL to the specified path."""
        if url is None or url == "":
            return "No URL specified. Please provide a URL to download.", False
        
        if path is None or path == "":
            return "No path specified. Please provide a path to save the downloaded file.", False
        
        try:
            # Prepare the request parameters
            request_kwargs = self.request_kwargs.copy() if self.request_kwargs is not None else {}
            request_kwargs["timeout"] = 30
            
            response = requests.get(url, **request_kwargs)
            response.raise_for_status()  # Raise exception for 4XX/5XX status codes
            
            # Expand user path and convert to absolute path
            filepath = os.path.abspath(os.path.expanduser(path))
            
            # Check if path is a directory
            if os.path.isdir(filepath):
                # Extract filename from URL or Content-Disposition header
                filename = None
                content_disposition = response.headers.get('Content-Disposition', '')
                if 'filename=' in content_disposition:
                    filename_match = re.search(r'filename=["\'](.*?)["\']', content_disposition)
                    if filename_match:
                        filename = filename_match.group(1)
                
                # If still no filename, extract it from URL
                if not filename:
                    filename = url.split('/')[-1].split('?')[0]
                    if not filename or filename == "":
                        filename = f"downloaded_file_{hash(url) % 10000}"
                
                # Sanitize filename
                filename = pathvalidate.sanitize_filename(filename)
                filepath = os.path.join(filepath, filename)
            
            # Ensure parent directory exists
            parent_dir = os.path.dirname(filepath)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
            
            # Ensure unique filename if file already exists
            if os.path.exists(filepath):
                counter = 1
                base_name, extension = os.path.splitext(filepath)
                while os.path.exists(filepath):
                    filepath = f"{base_name}_{counter}{extension}"
                    counter += 1
            
            # Save the content
            content_type = response.headers.get('Content-Type', '')
            
            # Write in binary mode
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            # Report success with file details
            size = len(response.content)
            size_str = f"{size} bytes"
            if size > 1024:
                size_str = f"{size/1024:.2f} KB"
            if size > 1024*1024:
                size_str = f"{size/(1024*1024):.2f} MB"
            
            return f"Successfully downloaded content from {url} to {filepath} ({size_str}, type: {content_type})", True
                
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if hasattr(e, 'response') and e.response is not None else 'unknown'
            return f"Failed to download content from {url}: HTTPError ({status_code}) - {str(e)}", False
        except requests.exceptions.Timeout:
            return f"Timeout error while downloading from {url}", False
        except requests.exceptions.ConnectionError as e:
            return f"Connection error while downloading from {url}: {str(e)}", False
        except OSError as e:
            return f"Failed to save file to {path}: {str(e)}", False
        except Exception as e:
            error_type = type(e).__name__
            return f"Failed to download content from {url}: {error_type} - {str(e)}", False

    def _split_pages(self) -> None:
        # Do not split search results
        if self.address.startswith("google:"):
            self.viewport_pages = [(0, len(self._page_content))]
            return

        # Handle empty pages
        if len(self._page_content) == 0:
            self.viewport_pages = [(0, 0)]
            return

        # Break the viewport into pages
        self.viewport_pages = []
        start_idx = 0
        while start_idx < len(self._page_content):
            end_idx = min(start_idx + self.viewport_size, len(self._page_content))  # type: ignore[operator]
            # Adjust to end on a space
            while end_idx < len(self._page_content) and self._page_content[end_idx - 1] not in [" ", "\t", "\r", "\n"]:
                end_idx += 1
            self.viewport_pages.append((start_idx, end_idx))
            start_idx = end_idx


    def _search_with_serper(self, query: str, filter_year: Optional[int] = None, filter_date: Optional[str] = None) -> None:
        """Search using Serper.dev service.
        
        Args:
            query: Search query string
            filter_year: Filter results to specific year (YYYY format)
            filter_date: Filter results before this date (YYYY-MM-DD format)
        """
        url = "https://google.serper.dev/search"
        
        payload = {
            "q": query,
            "num": 10  # Number of results
        }
        
        # Add date filter if specified
        # Priority: filter_date > filter_year
        if filter_date is not None:
            # Format: YYYY-MM-DD, filter to show results before this date
            # Google tbs format: cdr:1,cd_min:MM/DD/YYYY,cd_max:MM/DD/YYYY
            try:
                from datetime import datetime
                date_obj = datetime.strptime(filter_date, '%Y-%m-%d')
                # Set min date to a far past date (e.g., 2000-01-01)
                # Set max date to the filter_date
                min_date = "01/01/2000"
                max_date = date_obj.strftime('%m/%d/%Y')
                payload["tbs"] = f"cdr:1,cd_min:{min_date},cd_max:{max_date}"
            except ValueError as e:
                print(f"Invalid filter_date format '{filter_date}', expected YYYY-MM-DD: {e}")
        elif filter_year is not None:
            payload["tbs"] = f"cdr:1,cd_min:01/01/{filter_year},cd_max:12/31/{filter_year}"
        
        headers = {
            'X-API-KEY': self.serper_key,
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            results = response.json()
        except requests.exceptions.RequestException as e:
            print(f"Serper search error for query: {query}")
            raise Exception(f"Serper API error: {str(e)}")
        
        self.page_title = f"{query} - Search"
        
        # Check for errors
        if "error" in results:
            raise Exception(f"Serper API error: {results['error']}")
        
        # Check if we have organic results
        if "organic" not in results or len(results["organic"]) == 0:
            filter_message = ""
            if filter_date is not None:
                filter_message = f" with filter date before {filter_date}"
            elif filter_year is not None:
                filter_message = f" with filter year={filter_year}"
            self._set_page_content(
                f"No results found for '{query}'{filter_message}. Try with a more general query, or remove the date filter."
            )
            return
        
        def _prev_visit(url):
            for i in range(len(self.history) - 1, -1, -1):
                if self.history[i][0] == url:
                    return f"You previously visited this page {round(time.time() - self.history[i][1])} seconds ago.\n"
            return ""
        
        web_snippets: List[str] = list()
        
        for idx, page in enumerate(results["organic"], 1):
            title = page.get("title", "No title")
            link = page.get("link", "")
            snippet = page.get("snippet", "")
            
            # Extract date if available
            date_published = ""
            if "date" in page:
                date_published = "\nDate published: " + page["date"]
            
            # Extract source
            source = ""
            if "source" in page:
                source = "\nSource: " + page["source"]
            elif link:
                # Extract domain from URL as fallback
                from urllib.parse import urlparse
                domain = urlparse(link).netloc
                if domain:
                    source = f"\nSource: {domain}"
            
            entry = f"{idx}. [{title}]({link}){date_published}{source}\n{_prev_visit(link)}{snippet}"
            web_snippets.append(entry)
        
        content = (
            f"A Google search for '{query}' found {len(web_snippets)} results:\n\n## Web Results\n"
            + "\n\n".join(web_snippets)
        )
        
        self._set_page_content(content)

    def _serpapi_search(self, query: str, filter_year: Optional[int] = None) -> None:
        # print(f"Serper KEY: self.serper_key")
        if self.serper_key is not None:
            # Use Serper.dev if API key is provided
            self._search_with_serper(query, filter_year=filter_year)
            return

        raise NotImplementedError("SerpAPI search cannot be used for now.")
        if self.serpapi_key is None:
            raise ValueError("Missing SerpAPI key.")

        params = {
            "engine": "google",
            "q": query,
            "api_key": self.serpapi_key,
        }
        if filter_year is not None:
            params["tbs"] = f"cdr:1,cd_min:01/01/{filter_year},cd_max:12/31/{filter_year}"

        search = GoogleSearch(params)
        try:
            results = search.get_dict()
        except Exception as e:
            print(params["q"])
            raise e
        self.page_title = f"{query} - Search"
        if "error" in results.keys():
            if "Your account has run out of searches" in results["error"]:
                raise Exception("SerpAPI: Your account has run out of searches.")
        if "organic_results" not in results.keys():
            raise Exception(f"No results found for query: '{query}'. Use a less specific query.")
        if len(results["organic_results"]) == 0:
            year_filter_message = f" with filter year={filter_year}" if filter_year is not None else ""
            self._set_page_content(
                f"No results found for '{query}'{year_filter_message}. Try with a more general query, or remove the year filter."
            )
            return

        def _prev_visit(url):
            for i in range(len(self.history) - 1, -1, -1):
                if self.history[i][0] == url:
                    return f"You previously visited this page {round(time.time() - self.history[i][1])} seconds ago.\n"
            return ""

        web_snippets: List[str] = list()
        idx = 0
        if "organic_results" in results:
            for page in results["organic_results"]:
                idx += 1
                date_published = ""
                if "date" in page:
                    date_published = "\nDate published: " + page["date"]

                source = ""
                if "source" in page:
                    source = "\nSource: " + page["source"]

                snippet = ""
                if "snippet" in page:
                    snippet = "\n" + page["snippet"]

                redacted_version = f"{idx}. [{page['title']}]({page['link']}){date_published}{source}\n{_prev_visit(page['link'])}{snippet}"

                redacted_version = redacted_version.replace("Your browser can't play this video.", "")
                web_snippets.append(redacted_version)

        content = (
            f"A Google search for '{query}' found {len(web_snippets)} results:\n\n## Web Results\n"
            + "\n\n".join(web_snippets)
        )

        # print("SerpAPI results:", content)
        self._set_page_content(content)
    
    def _find_full_count(self, query: str) -> int:
        # get the number of matches for the query
        if query is None:
            return 0
        # Normalize the query, and convert to a regular expression
        nquery = re.sub(r"\*", "__STAR__", query)
        nquery = " " + (" ".join(re.split(r"\W+", nquery))).strip() + " "
        nquery = nquery.replace(" __STAR__ ", "__STAR__ ")  # Merge isolated stars with prior word
        nquery = nquery.replace("__STAR__", ".*").lower()
        
        if nquery.strip() == "":
            return 0
        idxs = list(range(len(self.viewport_pages)))
        count = 0
        for i in idxs:
            bounds = self.viewport_pages[i]
            content = self.page_content[bounds[0] : bounds[1]]
            
            ncontent = " " + (" ".join(re.split(r"\W+", content))).strip().lower() + " "
            # get the count on this page
            count += len(re.findall(nquery, ncontent))
        return count

    def _google_search(self, query: str, filter_year: Optional[int] = None) -> None:
        """Search using Google Custom Search API."""
        api_key = os.environ.get("GOOGLE_API_KEY")
        cx = os.environ.get("GOOGLE_CSE_ID")
        
        if not api_key or not cx:
            raise ValueError("Missing Google API key or Custom Search Engine ID. Set GOOGLE_API_KEY and GOOGLE_CSE_ID environment variables.")
        
        params = {
            "key": api_key,
            "cx": cx,
            "q": query,
            "num": 10,  # Number of results to return
        }
        
        if filter_year is not None:
            # Format date restriction for Custom Search API
            params["sort"] = "date:r:19000101:99991231"
            # Note: Custom Search API doesn't support year filtering as precisely as SerpAPI
            # This is an approximation
        
        try:
            response = requests.get("https://www.googleapis.com/customsearch/v1", params=params)
            response.raise_for_status()
            results = response.json()
            
            self.page_title = f"{query} - Search"
            
            if "items" not in results or len(results["items"]) == 0:
                year_filter_message = f" with filter year={filter_year}" if filter_year is not None else ""
                self._set_page_content(
                    f"No results found for '{query}'{year_filter_message}. Try with a more general query, or remove the year filter."
                )
                return
            
            def _prev_visit(url):
                for i in range(len(self.history) - 1, -1, -1):
                    if self.history[i][0] == url:
                        return f"You previously visited this page {round(time.time() - self.history[i][1])} seconds ago.\n"
                return ""
            
            web_snippets: List[str] = list()
            
            for idx, item in enumerate(results["items"], 1):
                title = item.get("title", "No title")
                link = item.get("link", "")
                snippet = item.get("snippet", "")
                
                # Check if there's a publication date
                date_published = ""
                if "pagemap" in item and "metatags" in item["pagemap"]:
                    for metatag in item["pagemap"]["metatags"]:
                        if "article:published_time" in metatag:
                            date_published = "\nDate published: " + metatag["article:published_time"]
                            break
                
                source = ""
                if "displayLink" in item:
                    source = "\nSource: " + item["displayLink"]
                
                entry = f"{idx}. [{title}]({link}){date_published}{source}\n{_prev_visit(link)}\n{snippet}"
                web_snippets.append(entry)
            
            content = (
                f"A Google search for '{query}' found {len(web_snippets)} results:\n\n## Web Results\n"
                + "\n\n".join(web_snippets)
            )
            
            self._set_page_content(content)
            
        except requests.exceptions.RequestException as e:
            print(f"Error performing Google search: {e}")
            self._set_page_content(f"Error performing Google search: {str(e)}")

    def _fetch_page(self, url: str) -> None:
        download_path = ""
        try:
            if url.startswith("file://"):
                download_path = os.path.normcase(os.path.normpath(unquote(url[7:])))
                res = self._mdconvert.convert_local(download_path)
                self.page_title = res.title
                self._set_page_content(res.text_content)
            else:
                # Prepare the request parameters
                request_kwargs = self.request_kwargs.copy() if self.request_kwargs is not None else {}
                request_kwargs["stream"] = True

                # Send a HTTP request to the URL
                response = requests.get(url, **request_kwargs)
                response.raise_for_status()

                # If the HTTP request was successful
                content_type = response.headers.get("content-type", "")

                # Text or HTML
                if "text/" in content_type.lower():
                    res = self._mdconvert.convert_response(response)
                    self.page_title = res.title
                    # res.text_content = clean_markup(res.text_content) # NOTE not very useful, removing
                    self._set_page_content(res.text_content)
                # A download
                else:
                    # Try producing a safe filename
                    fname = None
                    download_path = None
                    try:
                        fname = pathvalidate.sanitize_filename(os.path.basename(urlparse(url).path)).strip()
                        download_path = os.path.abspath(os.path.join(self.cache_folder, fname))

                        suffix = 0
                        while os.path.exists(download_path) and suffix < 1000:
                            suffix += 1
                            base, ext = os.path.splitext(fname)
                            new_fname = f"{base}__{suffix}{ext}"
                            download_path = os.path.abspath(os.path.join(self.cache_folder, new_fname))

                    except NameError:
                        pass

                    # No suitable name, so make one
                    if fname is None:
                        extension = mimetypes.guess_extension(content_type)
                        if extension is None:
                            extension = ".download"
                        fname = str(uuid.uuid4()) + extension
                        download_path = os.path.abspath(os.path.join(self.cache_folder, fname))

                    # Open a file for writing
                    with open(download_path, "wb") as fh:
                        for chunk in response.iter_content(chunk_size=512):
                            fh.write(chunk)

                    # Create a local URI and map it to the original URL
                    local_uri = pathlib.Path(download_path).as_uri()
                    self.address_mapper[local_uri] = url
                    self.set_address(local_uri)

        except UnsupportedFormatException as e:
            print(e)
            self.page_title = ("Download complete.",)
            self._set_page_content(f"# Download complete\n\nSaved file to '{download_path}'")
        except FileConversionException as e:
            print(e)
            self.page_title = ("Download complete.",)
            self._set_page_content(f"# Download complete\n\nSaved file to '{download_path}'")
        except FileNotFoundError:
            self.page_title = "Error 404"
            self._set_page_content(f"## Error 404\n\nFile not found: {download_path}")
        except requests.exceptions.RequestException as request_exception:
            try:
                self.page_title = f"Error {response.status_code}"

                # If the error was rendered in HTML we might as well render it
                content_type = response.headers.get("content-type", "")
                if content_type is not None and "text/html" in content_type.lower():
                    res = self._mdconvert.convert(response)
                    self.page_title = f"Error {response.status_code}"
                    self._set_page_content(f"## Error {response.status_code}\n\n{res.text_content}")
                else:
                    text = ""
                    for chunk in response.iter_content(chunk_size=512, decode_unicode=True):
                        text += chunk
                    self.page_title = f"Error {response.status_code}"
                    self._set_page_content(f"## Error {response.status_code}\n\n{text}")
            except NameError:
                self.page_title = "Error"
                self._set_page_content(f"## Error\n\n{str(request_exception)}")

    def _state(self) -> Tuple[str, str]:
        header = f"Address: {self.address}\n"
        if self.page_title is not None:
            header += f"Title: {self.page_title}\n"

        current_page = self.viewport_current_page
        total_pages = len(self.viewport_pages)

        address = self.address
        for i in range(len(self.history) - 2, -1, -1):  # Start from the second last
            if self.history[i][0] == address:
                header += f"You previously visited this page {round(time.time() - self.history[i][1])} seconds ago.\n"
                break

        header += f"Viewport position: Showing page {current_page + 1} of {total_pages}.\n"
        return (header, self.viewport)
