"""
Test configuration for MCAS backend tests.

Adds the worker app directory to sys.path so imports work
without requiring a package installation step.
"""
import sys
import os

# Add worker/app to path so we can import run_executor, execution, etc.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'worker', 'app'))
