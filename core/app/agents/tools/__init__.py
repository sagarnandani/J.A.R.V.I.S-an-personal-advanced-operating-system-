"""Tools agents may use.

A tool is the point where an agent stops reasoning and touches the world.
That makes it the right place to enforce permission -- not only in the
runtime, which knows what a task declared, but here, which knows what is
actually about to happen.

Both checks exist on purpose. The runtime check is the policy; this one
is the fact. A capability that forgets to declare NETWORK still cannot
reach the network, and the failure is a clear refusal rather than a
silent widening of what an agent can do.
"""
