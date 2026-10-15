"""Streamlit entrypoint for the hosted dashboard."""
import runpy


runpy.run_module('dashboard', init_globals={'HOSTED': True}, alter_sys=False)
