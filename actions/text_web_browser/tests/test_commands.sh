# First, activate the virtual environment
cd /home/ykw5399/mainfolder/osworld/actions/text_web_browser/
source .venv/bin/activate

# 1. Visit Microsoft Word Add-ins page
./bin/text_web_browser visit_page --url "https://learn.microsoft.com/en-us/office/dev/add-ins/reference/overview/word-add-ins-reference-overview"

# 2. Page down
./bin/text_web_browser page_down

# 3. Page down again
./bin/text_web_browser page_down

# 4. Page up
./bin/text_web_browser page_up

# 5. Search for "stateflow paper" on Google
./bin/text_web_browser visit_page --url "google:stateflow paper"

# 6. Find "stateflow" on the page
./bin/text_web_browser find_on_page --query "stateflow"

# 7. Find next occurrence
./bin/text_web_browser find_next