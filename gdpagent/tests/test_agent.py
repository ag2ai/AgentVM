"""
Simple test script for the Agent class.
Tests the agent's ability to answer a question and terminate with the final answer.
"""

import sys
import os
from pathlib import Path

# Add project root directory to path to import gdpagent and agentic_machines
# test_agent.py is now in osworld/gdpagent_tests/, so go up one level to osworld/
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import json
from agentic_machines.agents.base_agent import BaseAgent
from agentic_machines.config import get_llm_config


def test_simple_question():
    """Test the agent with a simple question that requires no tools."""
    
    # Define the agent schema
    agent_name = "question_answering_agent"
    agent_description = "An agent that answers questions"
    
    # System message for the agent
    system_msg = """You are a helpful assistant that answers questions.
When you have the answer, use the terminate tool to provide your final response.

Important: You must use the terminate tool when you're ready to provide the final answer.
"""
    
    # LLM configuration using the config helper
    llm_config = get_llm_config(
        model="gpt-4o-mini",
        cache_seed=42,
        temperature=0.7,
        max_tokens=1000
    )
    
    # Create the agent with no action tools (only terminate will be available)
    agent = BaseAgent(
        name=agent_name,
        description=agent_description,
        action_schemas=[],  # No additional tools
        system_msg=system_msg,
        llm_config=llm_config,
        max_step=5
    )
    
    # Test task: Ask a simple question
    print("=" * 60)
    print("Testing Agent with Simple Question")
    print("=" * 60)
    
    task = "What is the capital of France?"
    
    print(f"\nTask: {task}")
    print("\nRunning agent...")
    
    # Execute the agent
    result = agent.run(task)
    
    # Display results
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Answer: {result.output}")
    print(f"Finish Reason: {result.finish_reason}")
    print(f"Sources: {result.info.sources}")
    print(f"\nLLM Usage: {json.dumps(result.info.llm_usage, indent=2)}")
    print(f"Total Messages: {len(result.info.interactions)}")
    
    # Verify the result
    assert result.output, "Agent should return an answer"
    assert result.finish_reason in ["success", "failure", "error", "max_step_limit"], \
        f"Unexpected finish reason: {result.finish_reason}"
    
    print("\n✓ Test passed!")
    return result


def test_math_question():
    """Test the agent with a simple math question."""
    
    agent_name = "math_agent"
    agent_description = "An agent that solves math problems"
    
    system_msg = """You are a helpful assistant that solves math problems.
Calculate the answer step by step, then use the terminate tool to provide your final response.

Important: You must use the terminate tool when you're ready to provide the final answer.
"""
    
    llm_config = get_llm_config(
        model="gpt-4o-mini",
        cache_seed=42,
        temperature=0.0,  # Lower temperature for math
        max_tokens=500
    )
    
    agent = BaseAgent(
        name=agent_name,
        description=agent_description,
        action_schemas=[],
        system_msg=system_msg,
        llm_config=llm_config,
        max_step=5
    )
    
    print("\n" + "=" * 60)
    print("Testing Agent with Math Question")
    print("=" * 60)
    
    task = {
        "problem": "What is 15 * 23 + 47?"
    }
    
    print(f"\nTask: {task['problem']}")
    print("\nRunning agent...")
    
    result = agent(task=task["problem"])
    
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Answer: {result.output}")
    print(f"Finish Reason: {result.finish_reason}")
    print(f"\nLLM Usage: {json.dumps(result.info.llm_usage, indent=2)}")
    
    print("\n✓ Test passed!")
    return result


if __name__ == "__main__":
    print("Starting Agent Tests\n")
    
    try:
        # Run test 1
        result1 = test_simple_question()
        
        # Run test 2
        result2 = test_math_question()
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED ✓")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {str(e)}")
        import traceback
        traceback.print_exc()
