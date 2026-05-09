import os
import re
from typing import List, Optional, Tuple, Union
from dataclasses import dataclass, field
from .mdconvert import MarkdownConverter

@dataclass
class FileStatus:
    abs_path: str = "" # absolute path of the file
    full_content: str = "" # full content of the file
    curr_page_idx: int = 0 # current page index
    view_port_pages: List[Tuple[int, int]] = field(default_factory=list) # list of page bounds

    last_find_query: str = "" # last find query
    find_matches: List[int] = field(default_factory=list) # list of matched pages
    find_count: int = 0 # number of matched pages

    @property
    def viewport(self) -> str:
        """Return the content of the current viewport."""
        bounds = self.view_port_pages[self.curr_page_idx]
        return self.full_content[bounds[0] : bounds[1]]


FILE_NOT_OPEN_STR = "File not open: {path}. Please open the file first."

class FileEnv():
    """Served to read and manage multiple markdown files simultaneously."""

    def __init__( 
        self, 
        working_dir: str,
        viewport_size: Union[int, None] = 1024 * 8, 
        max_opened_files: int = 2, # can view 2 files at a time
    ):
        name: str = "FileEnv"
        description: str = "File environment for opening and reading files."
        self._name = name
        self._description = description
        self.working_dir = working_dir
        self.viewport_size = viewport_size  # Applies only to the standard uri types
        self._markdown_converter = MarkdownConverter()
        self.max_opened_files = max_opened_files

        self.open_files: dict[str, FileStatus] = {}
        self.closed_files: dict[str, FileStatus] = {}
        self.file_usage_order: List[str] = []  # for LRU tracking

        self.operation_history: List[str] = []
        self.current_view: str = ""
        # TODO: restrict the opened files to the working directory, add a parameter to enforce this

    def _touch_file(self, abs_path: str) -> None:
        """Mark file as recently used."""
        if abs_path in self.file_usage_order:
            self.file_usage_order.remove(abs_path)
        self.file_usage_order.append(abs_path)

    def _ensure_capacity(self) -> Optional[str]:
        # pop until max_opened_files is satisfied
        evict_info = ""
        while len(self.open_files) > self.max_opened_files:
            evict_file = self.file_usage_order.pop(0)
            evict_info += f"Evicted file: {evict_file}\n"
            self.closed_files[evict_file] = self.open_files.pop(evict_file)
        return evict_info.strip()
    
    def get_full_content(self, path: str) -> Optional[str]:
        """Get the full content of a file."""
        abs_path = os.path.abspath(path)
        # if not opened, open first
        if abs_path not in self.open_files:
            result, _ = self.action_open_file(path)
            if "Error" in result:
                return None
            
        # if opened, return the content
        file = self.open_files.get(abs_path)
        if file:
            return file.full_content

    def step(self, 
             action: str,
             path: str,
             query: Optional[str] = None
            ) -> str:
        if action == "open_file":
            result, is_success = self.action_open_file(path)
        elif action == "page_down":
            result, is_success = self.action_page_down(path)
        elif action == "page_up":
            result, is_success = self.action_page_up(path)
        elif action == "find":
            result, is_success = self.action_find_on_page(path, query)
        elif action == "find_next":
            result, is_success = self.action_find_next(path)
        else:
            raise ValueError(f"Unknown action: {action}")
        
        self.operation_history.append(
            {
                "action": action,
                "path": path,
                "query": query,
                "action_feedback": result,
                "opened_files": list(self.open_files.keys()),
            }
        )
        # Generate content from all currently opened files
        contents = []
        for file_path, file in self.open_files.items():
            header = f"[File: {file_path}] Page {file.curr_page_idx + 1} of {len(file.view_port_pages)}"
            body = file.viewport
            contents.append(f"{header}\n{'='*40}\n{body}")
        
        all_content = "\n\n".join(contents)
        self.current_view = all_content
        finish_reason="success" if is_success else "error"
        return f"""
[Action Result]
{finish_reason}: {result}
[File_Reader]
{all_content}
"""
        # EnvResult(
        #     output=all_content,
        #     name=self.name,
        #     finish_reason="success" if is_success else "error",
        #     # optional fields
        #     info=self.operation_history[-1]
        # )

    def reset(self) -> None:
        """Reset the environment."""
        self.open_files.clear()
        self.closed_files.clear()
        self.file_usage_order.clear()
        self.operation_history.clear()
        self.current_view = ""
    
    def close(self) -> None:
        """Close the environment."""
        self.reset()
    
    # def schema(self, customize_name: str = None, customize_description: str = None) -> dict:
    #     """Get the schema for the environment."""
    #     name = customize_name if customize_name is not None else self._name
    #     description = customize_description if customize_description is not None else self._description
    #     return get_function_schema(
    #         f=self.step,
    #         name=name,
    #         description=description,
    #         args_schema={
    #             "action": {
    #                 "type": "string",
    #                 "description": "Action to perform on the file.",
    #                 "enum": ["open_file", "page_up", "page_down", "find", "find_next"]
    #             },
    #             "path": {
    #                 "type": "string",
    #                 "description": "Path to the file."
    #             },
    #             "query": {
    #                 "type": "string",
    #                 "description": "Query to search for in the file."
    #             }
    #         }
    #     )
        
    # action
    def action_open_file(self, path: str) -> Tuple[str, bool]:
        abs_path = os.path.abspath(path)
        self._touch_file(abs_path)

        if abs_path in self.open_files:
            return f"File already open: {abs_path}", True

        if abs_path in self.closed_files:
            self.open_files[abs_path] = self.closed_files.pop(abs_path)
        else:
            try:
                if os.path.isdir(abs_path):
                    raise NotImplementedError("Directory browsing is not supported.")
                if not os.path.isfile(abs_path):
                    raise FileNotFoundError
                
                result = self._markdown_converter.convert(abs_path)
                content = result.text_content
                pages = self.split_pages(content, self.viewport_size)
                self.open_files[abs_path] = FileStatus(
                    abs_path=abs_path,
                    full_content=content,
                    view_port_pages=pages
                )
            except FileNotFoundError:
                return f"Error: File not found: {abs_path}", False
            except Exception as e:
                return f"Error opening file: {e}", False

        evict_info = self._ensure_capacity()

        file_name = os.path.basename(abs_path)
        if evict_info:
            return f"{evict_info}. Opened new file: {file_name}", True
        return f"Opened new file: {file_name}", True

    def action_page_up(self, path: str) ->  Tuple[str, bool]:
        file = self.open_files.get(os.path.abspath(path))
        if not file:
            # return FILE_NOT_OPEN_STR.format(path=path)
            # open file if not opened
            result, _ = self.action_open_file(path)
            if "Error" in result:
                return result, False
            file = self.open_files.get(os.path.abspath(path))
        prev_page = file.curr_page_idx + 1
        file.curr_page_idx = max(file.curr_page_idx - 1, 0)
        self._touch_file(path)
        return f"Moved from page {prev_page+1} to {file.curr_page_idx+1}", True

    def action_page_down(self, path: str) ->  Tuple[str, bool]:
        file = self.open_files.get(os.path.abspath(path))
        if not file:
            # 
            # open file if not opened
            result, _ = self.action_open_file(path)
            if "Error" in result:
                return result, False
            file = self.open_files.get(os.path.abspath(path))
        prev_page = file.curr_page_idx
        file.curr_page_idx = min(file.curr_page_idx + 1, len(file.view_port_pages) - 1)
        self._touch_file(path)
        return f"Moved from page {prev_page+1} to {file.curr_page_idx+1}", True

    def action_find_on_page(self, path: str, query: str) -> Tuple[str, bool]:
        if query == "" or query is None:
            return "No query specified. Please provide a query to search for."
        file = self.open_files.get(os.path.abspath(path))
        if not file:
            # return FILE_NOT_OPEN_STR.format(path=path)
            # open file if not opened
            result, _= self.action_open_file(path)
            if "Error" in result:
                return result, False
            file = self.open_files.get(os.path.abspath(path))
    
        normalized_query = re.sub(r"\*", "__STAR__", query)
        normalized_query = " " + (" ".join(re.split(r"\W+", normalized_query))).strip() + " "
        normalized_query = normalized_query.replace(" __STAR__ ", "__STAR__ ")
        normalized_query = normalized_query.replace("__STAR__", ".*").lower()

        matches = []
        for i, (start, end) in enumerate(file.view_port_pages):
            content = file.full_content[start:end]
            norm_content = " " + (" ".join(re.split(r"\W+", content))).strip().lower() + " "
            if re.search(normalized_query, norm_content):
                matches.append(i)

        file.last_find_query = query
        file.find_matches = matches
        file.find_count = len(matches)

        if matches:
            file.curr_page_idx = matches[0]
            self._touch_file(path)
            return f"Found {file.find_count} matches across {len(matches)} pages. Viewing first match on page {file.curr_page_idx + 1}", True
        else:
            return "No results found.", True

    def action_find_next(self, path: str) -> Tuple[str, bool]:
        file = self.open_files.get(os.path.abspath(path))
        if not file:
            # return FILE_NOT_OPEN_STR.format(path=path)
            # open file if not opened
            result, _ = self.action_open_file(path)
            if "Error" in result:
                return result, False
            file = self.open_files.get(os.path.abspath(path))

        if not file.last_find_query:
            return "No query specified. Use find_on_page first.", False
        if not file.find_matches:
            return f"No matches found for last query {file.last_find_query}. Please provide a new query.", False

        current_idx = file.find_matches.index(file.curr_page_idx) if file.curr_page_idx in file.find_matches else -1
        next_idx = (current_idx + 1) % len(file.find_matches)
        file.curr_page_idx = file.find_matches[next_idx]
        self._touch_file(path)
        return f"Moved to next match on page {file.curr_page_idx + 1}", True

    @staticmethod
    def split_pages(content: str, viewport_size) -> List[Tuple[int, int]]:
        if not content:
            return [(0, 0)]
        pages = []
        start_idx = 0
        while start_idx < len(content):
            end_idx = min(start_idx + viewport_size, len(content))
            while end_idx < len(content) and content[end_idx - 1] not in [" ", "\t", "\r", "\n"]:
                end_idx += 1
            pages.append((start_idx, end_idx))
            start_idx = end_idx
        return pages
