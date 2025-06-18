import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GObject

# Constants for spacing
NODE_MARGIN_X = 20
NODE_MARGIN_Y = 20 # Spacing WITHIN a branch
ROOT_BRANCH_MARGIN_Y = 40 # Larger spacing BETWEEN root branches
HORIZONTAL_PADDING = 0 # Extra padding INSIDE the buttons that acts as a margin

class TreeNode:
    def __init__(self, value):
        self.value = value
        self.children = []

class StagesTreeView(Gtk.Fixed):
    __gtype_name__ = 'StagesTreeView'

    def __init__(self):
        super().__init__()
        self.root_nodes = []
        self.node_positions = {}
        self.node_sizes = {}
        self.buttons = {}
        # NEW: A list to hold the actual Gtk.Separator widgets
        self.separators = []

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_draw_func(self.draw_func)
        self.put(self.drawing_area, 0, 0)

    def on_node_clicked(self, button, node):
        print(f"Button clicked for node: {node.value}")

    def set_root_nodes(self, root_nodes: list[TreeNode]):
        self.root_nodes = root_nodes
        self._layout_tree()
        self.drawing_area.queue_draw()

    def draw_func(self, area, context, width, height):
        # The separator drawing logic has been REMOVED from here.

        # --- Draw Node Connectors ---
        context.set_line_width(2)
        context.set_source_rgb(0.5, 0.5, 0.5)

        for parent, (px, py) in self.node_positions.items():
            parent_w, parent_h = self.node_sizes[parent]
            for child in parent.children:
                if child in self.node_positions and child in self.node_sizes:
                    child_x, child_y = self.node_positions[child]
                    child_w, child_h = self.node_sizes[child]

                    parent_visible_start_x = px + HORIZONTAL_PADDING
                    parent_visible_end_x = px + parent_w - HORIZONTAL_PADDING
                    child_visible_start_x = child_x + HORIZONTAL_PADDING
                    child_visible_end_x = child_x + child_w - HORIZONTAL_PADDING

                    if parent_visible_start_x <= child_visible_end_x - 5 and parent_visible_end_x >= child_visible_start_x + 5:
                        overlap_start = max(parent_visible_start_x, child_visible_start_x)
                        overlap_end = min(parent_visible_end_x, child_visible_end_x)
                        connector_x = (overlap_start + overlap_end) / 2
                        parent_connector_y = py + parent_h
                        child_connector_y = child_y
                        context.move_to(connector_x, parent_connector_y)
                        context.line_to(connector_x, child_connector_y)
                    else:
                        parent_cx = px + parent_w / 2
                        child_cx = child_x + child_w / 2
                        parent_connector_y = py + parent_h / 2
                        if child_cx > parent_cx:
                            parent_connector_x = parent_visible_end_x
                        else:
                            parent_connector_x = parent_visible_start_x
                        child_connector_x = child_cx
                        child_connector_y = child_y
                        context.move_to(parent_connector_x, parent_connector_y)
                        context.line_to(child_connector_x, parent_connector_y)
                        context.line_to(child_connector_x, child_connector_y)
                    context.stroke()

    def _get_all_nodes(self):
        all_nodes = []
        nodes_to_visit = list(self.root_nodes)
        visited = set()
        while nodes_to_visit:
            node = nodes_to_visit.pop(0)
            if node in visited: continue
            visited.add(node)
            all_nodes.append(node)
            nodes_to_visit.extend(node.children)
        return all_nodes

    def _measure_nodes(self):
        self.node_sizes = {}
        for node in self._get_all_nodes():
            label = str(getattr(node.value, "name", "Unnamed"))
            temp_button = Gtk.Button(label=label)
            temp_button.set_margin_start(HORIZONTAL_PADDING)
            temp_button.set_margin_end(HORIZONTAL_PADDING)
            min_size, nat_size = temp_button.get_preferred_size()
            self.node_sizes[node] = (nat_size.width, nat_size.height)

    def _layout_tree(self):
        if not self.root_nodes:
            return
        self._measure_nodes()

        # --- Clear all old widgets ---
        self.node_positions.clear()
        for button in self.buttons.values():
            self.remove(button)
        self.buttons.clear()
        # Remove old separator widgets from the container
        for sep in self.separators:
            self.remove(sep)
        self.separators.clear()

        # This list will temporarily hold the Y positions for the new separators
        separator_y_positions = []

        def layout_node(node, x, y):
            node_width, node_height = self.node_sizes[node]
            self.node_positions[node] = (x, y)
            if not node.children:
                return (x, x + node_width, y + node_height)
            child_y = y + node_height + NODE_MARGIN_Y
            current_child_x = x
            child_max_extents, child_max_y_positions = [], []
            for child in node.children:
                _min, max_x, max_y = layout_node(child, current_child_x, child_y)
                child_max_extents.append(max_x)
                child_max_y_positions.append(max_y)
                current_child_x = max_x + NODE_MARGIN_X
            subtree_min_x = x
            subtree_max_x = max(x + node_width, max(child_max_extents)) if child_max_extents else x + node_width
            subtree_max_y = max(y + node_height, max(child_max_y_positions)) if child_max_y_positions else y + node_height
            return (subtree_min_x, subtree_max_x, subtree_max_y)

        # --- Calculate positions and total size ---
        current_y, total_max_x = ROOT_BRANCH_MARGIN_Y, 0
        for i, root in enumerate(self.root_nodes):
            _min_x, max_x, max_y = layout_node(root, NODE_MARGIN_X, current_y)
            total_max_x = max(total_max_x, max_x)
            if i < len(self.root_nodes) - 1:
                separator_y = max_y + ROOT_BRANCH_MARGIN_Y / 2
                separator_y_positions.append(separator_y)
            current_y = max_y + ROOT_BRANCH_MARGIN_Y

        total_width = total_max_x + NODE_MARGIN_X
        total_height = current_y
        self.set_size_request(total_width, total_height)
        self.drawing_area.set_size_request(total_width, total_height)

        # --- Place all widgets ---
        # Place buttons
        for node, (x, y) in self.node_positions.items():
            label = str(getattr(node.value, "name", "Unnamed"))
            button = Gtk.Button(label=label)
            button.set_margin_start(HORIZONTAL_PADDING)
            button.set_margin_end(HORIZONTAL_PADDING)
            button.connect("clicked", self.on_node_clicked, node)
            self.put(button, x, y)
            self.buttons[node] = button

        # Place separators
        for y_pos in separator_y_positions:
            separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            separator.set_size_request(total_width - NODE_MARGIN_X * 2, -1) # Span the full width
            self.put(separator, NODE_MARGIN_X, y_pos)
            self.separators.append(separator)

