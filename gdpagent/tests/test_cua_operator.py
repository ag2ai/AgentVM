#!/usr/bin/env python3
"""Test script for CUAOperator."""

import base64
import logging
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_cua_operator")

# Setup OpenAI config
from agentic_machines.config import set_llm_caller_config
set_llm_caller_config(cache_seed=42)

def test_cua_operator():
    """Test CUAOperator with a screenshot and instruction."""
    
    try:
        from gdpagent.cua_operator import CUAOperator
    except ImportError as e:
        logger.error(f"Failed to import required modules: {e}")
        logger.error("Make sure you have the required dependencies installed")
        return False
    
    # Screenshot path
    screenshot_path = "gdpagent_tests/initial_screenshot.png"
    
    # Instruction
    instruction = "please open the NoxaPulse docx"
    
    logger.info("=" * 60)
    logger.info("Testing CUAOperator")
    logger.info("=" * 60)
    logger.info(f"Screenshot: {screenshot_path}")
    logger.info(f"Instruction: {instruction}")
    
    # Check if screenshot exists
    if not Path(screenshot_path).exists():
        logger.error(f"Screenshot not found at: {screenshot_path}")
        return False
    
    logger.info("✓ Screenshot file found")
    
    # Load and encode screenshot
    try:
        with open(screenshot_path, "rb") as f:
            screenshot_bytes = f.read()
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
        logger.info(f"✓ Screenshot loaded and encoded (size: {len(screenshot_bytes)} bytes)")
    except Exception as e:
        logger.error(f"Failed to load screenshot: {e}")
        return False
    
    # Create CUA operator
    try:
        cua_operator = CUAOperator(
            screen_width=1920,
            screen_height=1080,
            environment="linux"
        )
        logger.info("✓ CUAOperator initialized")
    except Exception as e:
        logger.error(f"Failed to initialize CUAOperator: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Prepare history inputs
    history_inputs = [{
        "role": "user",
        "content": [
            {"type": "input_text", "text": instruction},
            {"type": "input_image", "image_url": f"data:image/png;base64,{screenshot_b64}"},
        ],
    }]
    
    logger.info("✓ History inputs prepared")
    logger.info(f"  - Role: user")
    logger.info(f"  - Content items: 2 (text + image)")
    
    # Call the operator
    logger.info("\nCalling CUA API...")
    logger.info("-" * 60)
    
    try:
        result = cua_operator(messages=history_inputs)
        
        logger.info("✓ CUA API call successful!")
        logger.info(f"\nResults:")
        logger.info(f"  - Kind: {result.kind}")
        logger.info(f"  - Name: {result.name}")
        logger.info(f"  - Finish reason: {result.finish_reason}")
        logger.info(f"  - Cost: ${result.cost:.6f}")
        logger.info(f"  - Input items returned: {len(result.input_items)}")
        
        # Examine the response output and print the raw response in full
        if hasattr(result, 'raw_response') and result.raw_response:
            response = result.raw_response

            # Attempt to serialise the raw response into JSON-serialisable structures
            import json
            from pprint import pprint

            def _serialise(obj):
                # dicts are already serialisable
                if isinstance(obj, dict):
                    return {k: _serialise(v) for k, v in obj.items()}
                # Pydantic / autogen models may expose model_dump()
                if hasattr(obj, "model_dump"):
                    try:
                        return _serialise(obj.model_dump())
                    except Exception:
                        pass
                # autogen/OpenAI wrapper objects may have __dict__
                if hasattr(obj, "__dict__"):
                    try:
                        return {k: _serialise(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
                    except Exception:
                        pass
                # Sequences
                if isinstance(obj, (list, tuple)):
                    return [_serialise(v) for v in obj]
                # Fallback to string
                try:
                    return json.loads(json.dumps(obj))
                except Exception:
                    return str(obj)

            try:
                serial = _serialise(response)
                # Pretty-print the full raw response
                logger.info("\n----- FULL RAW RESPONSE (start) -----")
                try:
                    logger.info(json.dumps(serial, indent=2, default=str))
                except Exception:
                    # As a fallback, use pprint to avoid JSON serialization errors
                    pprint(serial)
                logger.info("----- FULL RAW RESPONSE (end) -----\n")
            except Exception as e:
                logger.error(f"Failed to serialise full raw response: {e}")

            logger.info(f"\nResponse details:")
            # Try to access output length safely
            output_items = []
            try:
                output_items = getattr(response, "output", []) or []
            except Exception:
                output_items = []
            logger.info(f"  - Output items: {len(output_items)}")

            # Print each output item type (expanded)
            for i, item in enumerate(output_items):
                try:
                    if isinstance(item, dict):
                        item_dict = item
                    elif hasattr(item, "model_dump"):
                        item_dict = item.model_dump()
                    elif hasattr(item, "__dict__"):
                        item_dict = {k: v for k, v in item.__dict__.items() if not k.startswith("_")}
                    else:
                        item_dict = {"raw": str(item)}
                except Exception:
                    item_dict = {"raw": str(item)}

                item_type = item_dict.get("type", "unknown")
                item_id = item_dict.get("id", "no-id")
                logger.info(f"    [{i}] Type: {item_type}, ID: {item_id}")

                # Print reasoning summary if available
                if item_type == "reasoning":
                    reasoning_summary = item_dict.get("summary", "")
                    if reasoning_summary:
                        logger.info(f"        Reasoning summary: {str(reasoning_summary)[:500]}")

                # Print message text if available
                if item_type == "message":
                    message_content = item_dict.get("content", [])
                    if message_content:
                        for content_item in message_content:
                            if isinstance(content_item, dict) and content_item.get("type") == "text":
                                text = content_item.get("text", "")
                                logger.info(f"        Message: {text}")

                # Print computer call info if available
                if item_type == "computer_call":
                    action = item_dict.get("action", {})
                    if isinstance(action, dict):
                        action_type = action.get("type", "unknown")
                        logger.info(f"        Action type: {action_type}")
                        if action_type == "mouse":
                            logger.info(f"        Mouse: button={action.get('button')}, x={action.get('x')}, y={action.get('y')}")
                        elif action_type == "keyboard":
                            logger.info(f"        Keyboard: text={action.get('text', '')}")
        
        logger.info("\n" + "=" * 60)
        logger.info("✓ Test completed successfully!")
        logger.info("=" * 60)
        return True
        
    except Exception as e:
        logger.error(f"✗ CUA API call failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_cua_operator()
    sys.exit(0 if success else 1)
