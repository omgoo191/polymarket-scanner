#!/usr/bin/env python3
"""
Root-level launcher. Run from project root:
  python run.py
"""
import sys
import os

# Ensure project root is on the path regardless of how this is invoked
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import main
main()
