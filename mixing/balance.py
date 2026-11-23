from collections import deque


def check_operation_balance(operation):
    inputs = [weight for _, weight in operation.inputs if weight is not None]
    outputs = [weight for _, weight in operation.outputs if weight is not None]
    input_total = sum(inputs)
    output_total = sum(outputs)
    if (len(inputs) == len(operation.inputs) and output_total > input_total
            or len(outputs) == len(operation.outputs) and input_total > output_total):
        raise ValueError(f'out-of-balance operation {operation.operation_id}')


def require_complete_weighings(operation):
    if operation.state == 'pending_weighings':
        raise ValueError(f'missing weighing in operation {operation.operation_id}')
    check_operation_balance(operation)


def maximum_flow(edges, source, sink):
    """Integral maximum flow by shortest augmenting paths in the residual graph."""
    residual = {source: {}, sink: {}}
    for start, end, weight in edges:
        if not weight:
            continue
        adjacency = residual.setdefault(start, {})
        adjacency[end] = adjacency.get(end, 0) + weight
        residual.setdefault(end, {}).setdefault(start, 0)

    total = 0
    while True:
        parents = {source: source}
        queue = deque([source])
        while queue and sink not in parents:
            node = queue.popleft()
            for neighbor, available in residual[node].items():
                if available > 0 and neighbor not in parents:
                    parents[neighbor] = node
                    queue.append(neighbor)
        if sink not in parents:
            return total

        path = []
        node = sink
        while node != source:
            parent = parents[node]
            path.append((parent, node))
            node = parent
        amount = min(residual[start][end] for start, end in path)
        for start, end in path:
            residual[start][end] -= amount
            residual[end][start] += amount
        total += amount


def require_feasible_measures(supplies, legs):
    """Test whether the missing measurements admit a conserved completion.

    The last vertex collects all terminal loads. Fixed edges contribute their
    net balance. Every missing edge can carry at most the full admitted mass.
    """
    total = sum(supplies)
    balance = [*supplies, -total]
    unknown_edges = []
    for start, end, weight in legs:
        if weight is None:
            unknown_edges.append((start, end, total))
        else:
            balance[start] -= weight
            balance[end] += weight

    supply_node = len(balance)
    demand_node = supply_node + 1
    needed = 0
    for node, remainder in enumerate(balance):
        if remainder > 0:
            unknown_edges.append((supply_node, node, remainder))
            needed += remainder
        elif remainder < 0:
            unknown_edges.append((node, demand_node, -remainder))
    if needed and maximum_flow(unknown_edges, supply_node, demand_node) != needed:
        raise ValueError('out-of-balance measured flow has no conserved completion')
