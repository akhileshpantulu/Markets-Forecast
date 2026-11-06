"""Hotel market stochastic forecasting engine.

The package is pure Python on top of numpy, pandas, scipy and statsmodels so
that it runs both locally and in the browser through Pyodide. Functions that do
file I/O on disk live at the edges (CLI modules); core functions accept bytes or
DataFrames.
"""

__version__ = "0.1.0"
