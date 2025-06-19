import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GObject

# Constants for spacing
NODE_MARGIN_X = 24
NODE_MARGIN_Y = 24 # Spacing WITHIN a branch
ROOT_BRANCH_MARGIN_Y = 40 # Larger spacing BETWEEN root branches
HORIZONTAL_PADDING = 0

class TreeNode:
    def __init__(self, value):
        self.value = value
        self.children = []

class StagesTreeView(Gtk.Fixed):
    __gtype_name__ = 'StagesTreeView'

    __gsignals__ = {
        "stage-selected": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,))
    }

    def __init__(self, centered=False):
        super().__init__()
        # 1. NEW: A property to control the layout style
        self.centered = centered

        self.root_nodes = []
        self.node_positions = {}
        self.node_sizes = {}
        self.buttons = {}
        self.separators = []

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_draw_func(self.draw_func)
        self.put(self.drawing_area, 0, 0)

    def on_node_clicked(self, button, node):
        self.emit("stage-selected", node.value)

    def set_root_nodes(self, root_nodes: list[TreeNode]):
        self.root_nodes = root_nodes
        self._layout_tree()
        self.drawing_area.queue_draw()

    def draw_func(self, area, context, width, height):
        # This drawing logic is robust enough to handle both layout styles
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
                    child_cx = child_x + child_w / 2

                    if child_cx >= parent_visible_start_x and child_cx <= parent_visible_end_x:
                        parent_connector_y = py + parent_h
                        child_connector_y = child_y
                        context.move_to(child_cx, parent_connector_y)
                        context.line_to(child_cx, child_connector_y)
                    else:
                        parent_cx = px + parent_w / 2
                        parent_connector_y = py + parent_h / 2
                        if child_cx > parent_cx:
                            parent_connector_x = parent_visible_end_x
                        else:
                            parent_connector_x = parent_visible_start_x
                        child_connector_y = child_y
                        context.move_to(parent_connector_x, parent_connector_y)
                        context.line_to(child_cx, parent_connector_y)
                        context.line_to(child_cx, child_connector_y)
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
        # --- COMMON SETUP ---
        if not self.root_nodes:
            for button in self.buttons.values(): self.remove(button)
            for sep in self.separators: self.remove(sep)
            self.buttons.clear()
            self.separators.clear()
            self.drawing_area.queue_draw()
            return

        self._measure_nodes()
        self.node_positions.clear()
        for button in self.buttons.values(): self.remove(button)
        for sep in self.separators: self.remove(sep)
        self.buttons.clear()
        self.separators.clear()

        all_positions = {}
        current_y = 0
        total_max_x = 0
        separator_y_positions = []

        # 2. CONDITIONAL LAYOUT LOGIC
        if self.centered:
            # --- Centered Layout Logic ---
            def layout_node_centered(node, x, y, branch_positions):
                node_width, node_height = self.node_sizes[node]
                if not node.children:
                    branch_positions[node] = (x, y)
                    return x, x + node_width
                child_y = y + node_height + NODE_MARGIN_Y
                children_ranges, current_x = [], x
                for child in node.children:
                    min_x, max_x = layout_node_centered(child, current_x, child_y, branch_positions)
                    children_ranges.append((min_x, max_x))
                    current_x = max_x + NODE_MARGIN_X
                children_min_x, children_max_x = children_ranges[0][0], children_ranges[-1][1]
                children_center_x = (children_min_x + children_max_x) / 2
                node_x = children_center_x - node_width / 2
                branch_positions[node] = (node_x, y)
                return min(node_x, children_min_x), max(node_x + node_width, children_max_x)

            for i, root in enumerate(self.root_nodes):
                branch_positions, max_y_in_branch = {}, 0
                min_x, _ = layout_node_centered(root, 0, 0, branch_positions)
                horizontal_shift = -min_x + NODE_MARGIN_X
                for node, (x, y) in branch_positions.items():
                    final_x, final_y = x + horizontal_shift, y + current_y
                    all_positions[node] = (final_x, final_y)
                    node_w, node_h = self.node_sizes[node]
                    total_max_x = max(total_max_x, final_x + node_w)
                    max_y_in_branch = max(max_y_in_branch, final_y + node_h)
                if i < len(self.root_nodes) - 1:
                    separator_y_positions.append(max_y_in_branch + ROOT_BRANCH_MARGIN_Y / 2)
                current_y = max_y_in_branch + ROOT_BRANCH_MARGIN_Y

        else:
            # --- Left-Aligned Layout Logic ---
            def layout_node_left(node, x, y):
                node_width, node_height = self.node_sizes[node]
                all_positions[node] = (x, y)
                if not node.children: return (x, x + node_width, y + node_height)
                child_y = y + node_height + NODE_MARGIN_Y
                current_child_x, child_max_extents, child_max_y_positions = x, [], []
                for child in node.children:
                    _min, max_x_child, max_y_child = layout_node_left(child, current_child_x, child_y)
                    child_max_extents.append(max_x_child)
                    child_max_y_positions.append(max_y_child)
                    current_child_x = max_x_child + NODE_MARGIN_X
                subtree_max_x = max(x + node_width, max(child_max_extents)) if child_max_extents else x + node_width
                subtree_max_y = max(y + node_height, max(child_max_y_positions)) if child_max_y_positions else y + node_height
                return (x, subtree_max_x, subtree_max_y)

            for i, root in enumerate(self.root_nodes):
                _min_x, max_x, max_y = layout_node_left(root, NODE_MARGIN_X, current_y)
                total_max_x = max(total_max_x, max_x)
                if i < len(self.root_nodes) - 1:
                    separator_y_positions.append(max_y + ROOT_BRANCH_MARGIN_Y / 2)
                current_y = max_y + ROOT_BRANCH_MARGIN_Y

        # --- COMMON TEARDOWN ---
        self.node_positions = all_positions
        total_width = total_max_x + NODE_MARGIN_X
        total_height = current_y
        self.set_size_request(total_width, total_height)
        self.drawing_area.set_size_request(total_width, total_height)

        for node, (x, y) in self.node_positions.items():
            label = str(getattr(node.value, "name", "Unnamed"))
            button = Gtk.Button(label=label)
            button.set_margin_start(HORIZONTAL_PADDING)
            button.set_margin_end(HORIZONTAL_PADDING)
            button.connect("clicked", self.on_node_clicked, node)
            self.put(button, x, y)
            self.buttons[node] = button

        for y_pos in separator_y_positions:
            separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            separator.set_size_request(total_width - NODE_MARGIN_X * 2, -1)
            self.put(separator, NODE_MARGIN_X, y_pos)
            self.separators.append(separator)

