import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GObject

# Constants for spacing
NODE_MARGIN_X = 20
NODE_MARGIN_Y = 20
HORIZONTAL_PADDING = 10 # Extra padding INSIDE the buttons that acts as a margin

class TreeNode:
    def __init__(self, value):
        self.value = value
        self.children = []

class Stage:
    def __init__(self, id, name, parent_id=None):
        self.id = id
        self.name = name
        self.parent_id = parent_id

    def __repr__(self):
        return f"Stage(id={self.id}, name='{self.name}')"

class StagesTreeView(Gtk.Fixed):
    __gtype_name__ = 'StagesTreeView'

    def __init__(self):
        super().__init__()
        self.root_nodes = []
        self.node_positions = {}
        self.node_sizes = {}
        self.buttons = {}

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
        context.set_line_width(2)
        context.set_source_rgb(0.5, 0.5, 0.5)

        for parent, (px, py) in self.node_positions.items():
            parent_w, parent_h = self.node_sizes[parent]

            for child in parent.children:
                if child in self.node_positions and child in self.node_sizes:
                    child_x, child_y = self.node_positions[child]
                    child_w, child_h = self.node_sizes[child]

                    # --- FINAL CORRECTED OVERLAP DETECTION ---
                    # Calculate the coordinates for the VISIBLE part of the buttons,
                    # excluding the margins that are part of the allocation.
                    parent_visible_start_x = px + HORIZONTAL_PADDING
                    parent_visible_end_x = px + parent_w - HORIZONTAL_PADDING
                    child_visible_start_x = child_x + HORIZONTAL_PADDING
                    child_visible_end_x = child_x + child_w - HORIZONTAL_PADDING

                    # Perform the overlap check on the VISIBLE boundaries only.
                    if parent_visible_start_x <= child_visible_end_x and parent_visible_end_x >= child_visible_start_x:
                        # --- PATH 1: VISIBLE OVERLAP DETECTED ---
                        # Calculate the center of the VISIBLE overlap.
                        overlap_start = max(parent_visible_start_x, child_visible_start_x)
                        overlap_end = min(parent_visible_end_x, child_visible_end_x)
                        connector_x = (overlap_start + overlap_end) / 2

                        parent_connector_y = py + parent_h
                        child_connector_y = child_y

                        context.move_to(connector_x, parent_connector_y)
                        context.line_to(connector_x, child_connector_y)

                    else:
                        # --- PATH 2: NO VISIBLE OVERLAP ---
                        # Draw an elbow connector from the parent's visible side edge.
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
        self.node_positions.clear()
        for button in self.buttons.values():
            self.remove(button)
        self.buttons.clear()

        def layout_node(node, x, y):
            node_width, node_height = self.node_sizes[node]
            self.node_positions[node] = (x, y)
            if not node.children:
                return (x, x + node_width, y + node_height)
            child_y = y + node_height + NODE_MARGIN_Y
            current_child_x = x
            child_max_extents = []
            child_max_y_positions = []
            for child in node.children:
                _min, max_x, max_y = layout_node(child, current_child_x, child_y)
                child_max_extents.append(max_x)
                child_max_y_positions.append(max_y)
                current_child_x = max_x + NODE_MARGIN_X
            subtree_min_x = x
            subtree_max_x = max(x + node_width, max(child_max_extents)) if child_max_extents else x + node_width
            subtree_max_y = max(y + node_height, max(child_max_y_positions)) if child_max_y_positions else y + node_height
            return (subtree_min_x, subtree_max_x, subtree_max_y)

        current_y = NODE_MARGIN_Y
        total_max_x = 0
        for root in self.root_nodes:
            _min_x, max_x, max_y = layout_node(root, NODE_MARGIN_X, current_y)
            total_max_x = max(total_max_x, max_x)
            current_y = max_y + NODE_MARGIN_Y

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


if __name__ == '__main__':
    def build_tree_from_list(items):
        node_map = {item.id: TreeNode(item) for item in items}
        root_nodes = []
        for item in items:
            node = node_map[item.id]
            if item.parent_id and item.parent_id in node_map:
                parent_node = node_map[item.parent_id]
                parent_node.children.append(node)
            else:
                root_nodes.append(node)
        return root_nodes

    stages_data = [
        # Case 1: True overlap -> Vertical line
        Stage(id=1, name="Wide Parent"),
        Stage(id=2, name="Overlapping Child", parent_id=1),

        # Case 2: NO VISIBLE overlap -> Elbow connector
        # Their allocations overlap, but their visible parts do not.
        Stage(id=3, name="Root For Elbow Test"),
        Stage(id=4, name="First Child", parent_id=3),
        Stage(id=5, name="Second Child (Elbow from Side)", parent_id=3),

        # This is the key test case that was failing before
        Stage(id=6, name="False Positive Parent"),
        Stage(id=7, name="False Positive Child", parent_id=6)
    ]

    class ExampleApp(Gtk.Application):
        def do_activate(self):
            win = Gtk.ApplicationWindow(application=self, title="Stages Tree (Pixel Perfect)")
            win.set_default_size(800, 600)

            scrolled_window = Gtk.ScrolledWindow()
            scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            win.set_child(scrolled_window)

            tree_view = StagesTreeView()
            scrolled_window.set_child(tree_view)

            root_nodes = build_tree_from_list(stages_data)
            tree_view.set_root_nodes(root_nodes)

            win.present()

    app = ExampleApp()
    app.run()
