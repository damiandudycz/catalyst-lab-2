# Allows running examples as: python3 examples
import os, runpy
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "create_rpi5_project.py"), run_name="__main__")
