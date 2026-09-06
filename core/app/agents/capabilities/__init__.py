"""Real capabilities, as opposed to the mocks that proved the foundation.

Each module here registers one capability and its implementation. Keeping
them apart from `builtin.py` is the point: the mocks exist to test the
machinery, these exist to do work, and mixing them would make it unclear
which is which the moment there are ten.
"""
