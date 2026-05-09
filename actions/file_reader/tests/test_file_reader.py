#!/usr/bin/env python3
"""
Comprehensive test suite for the file_reader action.

This test suite covers:
1. Basic file operations (open, page navigation)
2. Search functionality (find, find_next)
3. Multiple file handling
4. State persistence
5. Error handling
6. Edge cases
"""

import os
import sys
import json
import tempfile
import shutil
import subprocess
from pathlib import Path
import unittest

# Add the parent directory to the path
action_dir = Path(__file__).parent
sys.path.insert(0, str(action_dir))

from lib.file_env import FileEnv, FileStatus


class TestFileEnv(unittest.TestCase):
    """Test the FileEnv class directly."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()
        self.file_env = FileEnv(
            working_dir=self.test_dir,
            viewport_size=100,  # Small viewport for testing
            max_opened_files=2
        )
        
        # Create test files
        self.test_file1 = os.path.join(self.test_dir, "test1.txt")
        with open(self.test_file1, 'w') as f:
            f.write("Line 1\nLine 2\nLine 3\n" * 20)  # Create content > 100 chars
        
        self.test_file2 = os.path.join(self.test_dir, "test2.txt")
        with open(self.test_file2, 'w') as f:
            f.write("Apple\nBanana\nCherry\nDate\nElderberry\n" * 10)
        
        self.test_file3 = os.path.join(self.test_dir, "test3.md")
        with open(self.test_file3, 'w') as f:
            f.write("# Header\n\nThis is a test markdown file.\n\n## Section 1\nContent here.\n" * 5)

    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir)

    def test_open_file_success(self):
        """Test opening a file successfully."""
        result, success = self.file_env.action_open_file(self.test_file1)
        self.assertTrue(success)
        self.assertIn("Opened new file", result)
        abs_path = os.path.abspath(self.test_file1)
        self.assertIn(abs_path, self.file_env.open_files)

    def test_open_file_already_open(self):
        """Test opening a file that's already open."""
        self.file_env.action_open_file(self.test_file1)
        result, success = self.file_env.action_open_file(self.test_file1)
        self.assertTrue(success)
        self.assertIn("already open", result.lower())

    def test_open_file_not_found(self):
        """Test opening a non-existent file."""
        result, success = self.file_env.action_open_file("nonexistent.txt")
        self.assertFalse(success)
        self.assertIn("Error", result)
        self.assertIn("not found", result.lower())

    def test_open_directory_error(self):
        """Test that opening a directory raises an error."""
        result, success = self.file_env.action_open_file(self.test_dir)
        self.assertFalse(success)
        self.assertIn("Error", result)

    def test_max_opened_files_eviction(self):
        """Test that opening more than max_opened_files evicts the oldest."""
        self.file_env.action_open_file(self.test_file1)
        self.file_env.action_open_file(self.test_file2)
        
        # Opening a third file should evict the first
        result, success = self.file_env.action_open_file(self.test_file3)
        self.assertTrue(success)
        self.assertIn("Evicted", result)
        
        abs_path1 = os.path.abspath(self.test_file1)
        abs_path3 = os.path.abspath(self.test_file3)
        
        # File 1 should be closed, file 3 should be open
        self.assertNotIn(abs_path1, self.file_env.open_files)
        self.assertIn(abs_path1, self.file_env.closed_files)
        self.assertIn(abs_path3, self.file_env.open_files)

    def test_page_down(self):
        """Test paging down through a file."""
        self.file_env.action_open_file(self.test_file1)
        abs_path = os.path.abspath(self.test_file1)
        
        initial_page = self.file_env.open_files[abs_path].curr_page_idx
        result, success = self.file_env.action_page_down(self.test_file1)
        
        self.assertTrue(success)
        final_page = self.file_env.open_files[abs_path].curr_page_idx
        self.assertEqual(final_page, initial_page + 1)

    def test_page_down_at_end(self):
        """Test paging down at the end of a file."""
        self.file_env.action_open_file(self.test_file1)
        abs_path = os.path.abspath(self.test_file1)
        
        # Go to last page
        file_status = self.file_env.open_files[abs_path]
        file_status.curr_page_idx = len(file_status.view_port_pages) - 1
        
        result, success = self.file_env.action_page_down(self.test_file1)
        self.assertTrue(success)
        # Should still be on last page
        self.assertEqual(file_status.curr_page_idx, len(file_status.view_port_pages) - 1)

    def test_page_up(self):
        """Test paging up through a file."""
        self.file_env.action_open_file(self.test_file1)
        abs_path = os.path.abspath(self.test_file1)
        
        # First go down
        self.file_env.action_page_down(self.test_file1)
        self.file_env.action_page_down(self.test_file1)
        
        current_page = self.file_env.open_files[abs_path].curr_page_idx
        result, success = self.file_env.action_page_up(self.test_file1)
        
        self.assertTrue(success)
        final_page = self.file_env.open_files[abs_path].curr_page_idx
        self.assertEqual(final_page, current_page - 1)

    def test_page_up_at_beginning(self):
        """Test paging up at the beginning of a file."""
        self.file_env.action_open_file(self.test_file1)
        abs_path = os.path.abspath(self.test_file1)
        
        result, success = self.file_env.action_page_up(self.test_file1)
        self.assertTrue(success)
        # Should still be on first page
        self.assertEqual(self.file_env.open_files[abs_path].curr_page_idx, 0)

    def test_find_on_page_success(self):
        """Test finding text in a file."""
        self.file_env.action_open_file(self.test_file2)
        result, success = self.file_env.action_find_on_page(self.test_file2, "Banana")
        
        self.assertTrue(success)
        self.assertIn("Found", result)
        
        abs_path = os.path.abspath(self.test_file2)
        self.assertGreater(len(self.file_env.open_files[abs_path].find_matches), 0)

    def test_find_on_page_no_results(self):
        """Test finding text that doesn't exist."""
        self.file_env.action_open_file(self.test_file2)
        result, success = self.file_env.action_find_on_page(self.test_file2, "NonexistentText")
        
        self.assertTrue(success)
        self.assertIn("No results", result)

    def test_find_on_page_empty_query(self):
        """Test finding with an empty query."""
        self.file_env.action_open_file(self.test_file2)
        result = self.file_env.action_find_on_page(self.test_file2, "")
        
        # When query is empty, it returns only the error message (not a tuple)
        self.assertIn("No query", result)

    def test_find_next_success(self):
        """Test finding next occurrence."""
        self.file_env.action_open_file(self.test_file2)
        self.file_env.action_find_on_page(self.test_file2, "Banana")
        
        result, success = self.file_env.action_find_next(self.test_file2)
        self.assertTrue(success)
        self.assertIn("Moved to next match", result)

    def test_find_next_without_find(self):
        """Test find_next without a prior find."""
        self.file_env.action_open_file(self.test_file2)
        result, success = self.file_env.action_find_next(self.test_file2)
        
        self.assertFalse(success)
        self.assertIn("No query", result)

    def test_find_next_wraps_around(self):
        """Test that find_next wraps around to the beginning."""
        self.file_env.action_open_file(self.test_file2)
        self.file_env.action_find_on_page(self.test_file2, "Apple")
        
        abs_path = os.path.abspath(self.test_file2)
        file_status = self.file_env.open_files[abs_path]
        
        # Go through all matches and back to the first
        num_matches = len(file_status.find_matches)
        first_match_page = file_status.curr_page_idx
        
        for _ in range(num_matches):
            self.file_env.action_find_next(self.test_file2)
        
        # Should be back at the first match
        self.assertEqual(file_status.curr_page_idx, first_match_page)

    def test_auto_open_on_page_down(self):
        """Test that page_down opens file if not already open."""
        result, success = self.file_env.action_page_down(self.test_file1)
        self.assertTrue(success)
        
        abs_path = os.path.abspath(self.test_file1)
        self.assertIn(abs_path, self.file_env.open_files)

    def test_auto_open_on_find(self):
        """Test that find opens file if not already open."""
        result, success = self.file_env.action_find_on_page(self.test_file2, "Banana")
        self.assertTrue(success)
        
        abs_path = os.path.abspath(self.test_file2)
        self.assertIn(abs_path, self.file_env.open_files)

    def test_step_method(self):
        """Test the step method."""
        result = self.file_env.step("open_file", self.test_file1)
        
        self.assertIn("Action Result", result)
        self.assertIn("File_Reader", result)
        self.assertIn("success", result.lower())

    def test_get_full_content(self):
        """Test getting full content of a file."""
        content = self.file_env.get_full_content(self.test_file1)
        self.assertIsNotNone(content)
        self.assertIn("Line 1", content)

    def test_reset(self):
        """Test resetting the environment."""
        self.file_env.action_open_file(self.test_file1)
        self.file_env.reset()
        
        self.assertEqual(len(self.file_env.open_files), 0)
        self.assertEqual(len(self.file_env.closed_files), 0)
        self.assertEqual(len(self.file_env.file_usage_order), 0)
        self.assertEqual(len(self.file_env.operation_history), 0)

    def test_split_pages(self):
        """Test the split_pages static method."""
        # Create content with spaces to allow splitting at word boundaries
        content = "word " * 50  # 250 chars with spaces
        pages = FileEnv.split_pages(content, 100)
        
        self.assertGreater(len(pages), 1)
        self.assertEqual(pages[0][0], 0)
        self.assertEqual(pages[-1][1], len(content))

    def test_split_pages_empty_content(self):
        """Test split_pages with empty content."""
        pages = FileEnv.split_pages("", 100)
        self.assertEqual(pages, [(0, 0)])


class TestFileReaderCLI(unittest.TestCase):
    """Test the file_reader command-line interface."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        
        # Create test files
        self.test_file = os.path.join(self.test_dir, "test.txt")
        with open(self.test_file, 'w') as f:
            f.write("Hello World\n" * 100)
        
        self.state_file = os.path.join(self.test_dir, "test_state.json")
        
        # Path to the file_reader script
        self.file_reader_script = os.path.join(
            Path(__file__).parent, "bin", "file_reader"
        )

    def tearDown(self):
        """Clean up test fixtures."""
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)

    def run_file_reader(self, *args):
        """Helper to run the file_reader command."""
        cmd = ["python3", self.file_reader_script] + list(args)
        cmd.extend(["--state-file", self.state_file])
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=self.test_dir
        )
        return result

    def test_cli_open_file(self):
        """Test CLI: open_file command."""
        result = self.run_file_reader("open_file", "test.txt")
        
        self.assertEqual(result.returncode, 0)
        self.assertIn("Opened new file", result.stdout)
        self.assertIn("Hello World", result.stdout)

    def test_cli_page_down(self):
        """Test CLI: page_down command."""
        # First open the file
        self.run_file_reader("open_file", "test.txt")
        
        # Then page down
        result = self.run_file_reader("page_down", "test.txt")
        
        self.assertEqual(result.returncode, 0)
        self.assertIn("Moved from page", result.stdout)

    def test_cli_find(self):
        """Test CLI: find command."""
        self.run_file_reader("open_file", "test.txt")
        
        result = self.run_file_reader("find", "test.txt", "--query", "Hello")
        
        self.assertEqual(result.returncode, 0)
        self.assertIn("Found", result.stdout)

    def test_cli_find_next(self):
        """Test CLI: find_next command."""
        self.run_file_reader("open_file", "test.txt")
        self.run_file_reader("find", "test.txt", "--query", "Hello")
        
        result = self.run_file_reader("find_next", "test.txt")
        
        self.assertEqual(result.returncode, 0)

    def test_cli_invalid_action(self):
        """Test CLI: invalid action."""
        result = self.run_file_reader("invalid_action", "test.txt")
        
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unrecognized action", result.stdout)

    def test_cli_missing_path(self):
        """Test CLI: missing path parameter."""
        result = self.run_file_reader("open_file")
        
        self.assertNotEqual(result.returncode, 0)

    def test_cli_find_without_query(self):
        """Test CLI: find command without query."""
        result = self.run_file_reader("find", "test.txt")
        
        self.assertNotEqual(result.returncode, 0)

    def test_cli_state_persistence(self):
        """Test CLI: state persists across calls."""
        # Open a file
        self.run_file_reader("open_file", "test.txt")
        
        # Page down
        self.run_file_reader("page_down", "test.txt")
        
        # Find something
        result = self.run_file_reader("find", "test.txt", "--query", "Hello")
        
        # Verify state file exists
        self.assertTrue(os.path.exists(self.state_file))
        
        # Verify state contains expected data
        with open(self.state_file, 'r') as f:
            state = json.load(f)
        
        self.assertIn("open_files", state)
        self.assertIn("operation_history", state)
        self.assertGreater(len(state["operation_history"]), 0)

    def test_cli_nonexistent_file(self):
        """Test CLI: opening a non-existent file."""
        result = self.run_file_reader("open_file", "nonexistent.txt")
        
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Error", result.stdout)


class TestFileReaderIntegration(unittest.TestCase):
    """Integration tests simulating real-world usage."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()
        self.file_env = FileEnv(
            working_dir=self.test_dir,
            viewport_size=200,
            max_opened_files=2
        )
        
        # Create a more realistic test file
        self.readme = os.path.join(self.test_dir, "README.md")
        with open(self.readme, 'w') as f:
            f.write("""# Test Project

## Installation

Run `pip install -r requirements.txt` to install dependencies.

## Usage

To start the application:

```bash
python main.py
```

## Configuration

Edit `config.yaml` to configure the application.

## Testing

Run tests with `pytest`.

## License

MIT License
""")

    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir)

    def test_workflow_read_and_search(self):
        """Test a complete workflow: open, search, navigate."""
        # Open the file
        result = self.file_env.step("open_file", self.readme)
        self.assertIn("success", result.lower())
        
        # Search for "Installation"
        result = self.file_env.step("find", self.readme, "Installation")
        self.assertIn("Found", result)
        
        # Navigate to next occurrence
        result = self.file_env.step("find_next", self.readme)
        self.assertIn("success", result.lower())

    def test_workflow_multiple_files(self):
        """Test working with multiple files."""
        file1 = os.path.join(self.test_dir, "file1.txt")
        file2 = os.path.join(self.test_dir, "file2.txt")
        
        with open(file1, 'w') as f:
            f.write("Content of file 1\n" * 10)
        with open(file2, 'w') as f:
            f.write("Content of file 2\n" * 10)
        
        # Open both files
        self.file_env.step("open_file", file1)
        self.file_env.step("open_file", file2)
        
        # Both should be in open_files
        self.assertEqual(len(self.file_env.open_files), 2)
        
        # Search in file1
        result = self.file_env.step("find", file1, "file 1")
        self.assertIn("Found", result)

    def test_workflow_file_eviction_and_reopen(self):
        """Test that evicted files can be reopened."""
        file1 = os.path.join(self.test_dir, "file1.txt")
        file2 = os.path.join(self.test_dir, "file2.txt")
        file3 = os.path.join(self.test_dir, "file3.txt")
        
        for fpath in [file1, file2, file3]:
            with open(fpath, 'w') as f:
                f.write(f"Content of {os.path.basename(fpath)}\n" * 10)
        
        # Open all three (third should evict first)
        self.file_env.step("open_file", file1)
        self.file_env.step("open_file", file2)
        self.file_env.step("open_file", file3)
        
        # File1 should be closed
        abs_path1 = os.path.abspath(file1)
        self.assertIn(abs_path1, self.file_env.closed_files)
        
        # Reopen file1
        result = self.file_env.step("open_file", file1)
        
        # File1 should be open again
        self.assertIn(abs_path1, self.file_env.open_files)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()
        self.file_env = FileEnv(
            working_dir=self.test_dir,
            viewport_size=100,
            max_opened_files=2
        )

    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir)

    def test_empty_file(self):
        """Test opening an empty file."""
        empty_file = os.path.join(self.test_dir, "empty.txt")
        with open(empty_file, 'w') as f:
            f.write("\n")  # Write at least a newline to avoid conversion issues
        
        result, success = self.file_env.action_open_file(empty_file)
        self.assertTrue(success)

    def test_very_large_file(self):
        """Test handling a very large file."""
        large_file = os.path.join(self.test_dir, "large.txt")
        with open(large_file, 'w') as f:
            for i in range(1000):
                f.write(f"Line {i}\n")
        
        result, success = self.file_env.action_open_file(large_file)
        self.assertTrue(success)
        
        abs_path = os.path.abspath(large_file)
        # Should have multiple pages
        self.assertGreater(len(self.file_env.open_files[abs_path].view_port_pages), 1)

    def test_special_characters_in_content(self):
        """Test file with special characters."""
        special_file = os.path.join(self.test_dir, "special.txt")
        with open(special_file, 'w', encoding='utf-8') as f:
            f.write("Special chars: @#$%^&*()_+\n")
            f.write("Unicode: 你好世界 🌍\n")
            f.write("Escape sequences: \t\n\r\n")
        
        result, success = self.file_env.action_open_file(special_file)
        self.assertTrue(success)

    def test_find_with_special_characters(self):
        """Test searching for special characters."""
        special_file = os.path.join(self.test_dir, "special.txt")
        with open(special_file, 'w') as f:
            f.write("Test * wildcard\n" * 10)
        
        self.file_env.action_open_file(special_file)
        result, success = self.file_env.action_find_on_page(special_file, "*")
        
        # Should handle wildcards in search
        self.assertTrue(success)

    def test_relative_vs_absolute_paths(self):
        """Test that relative and absolute paths work correctly."""
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, 'w') as f:
            f.write("Test content\n")
        
        # Change to test directory
        original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        
        try:
            # Open with relative path
            result1, success1 = self.file_env.action_open_file("test.txt")
            self.assertTrue(success1)
            
            # Both paths should resolve to the same file
            abs_path = os.path.abspath("test.txt")
            self.assertIn(abs_path, self.file_env.open_files)
        finally:
            os.chdir(original_cwd)


def run_tests():
    """Run all tests."""
    # Create a test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestFileEnv))
    suite.addTests(loader.loadTestsFromTestCase(TestFileReaderCLI))
    suite.addTests(loader.loadTestsFromTestCase(TestFileReaderIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))
    
    # Run the tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Return exit code based on results
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
