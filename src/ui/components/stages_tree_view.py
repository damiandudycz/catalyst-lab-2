import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GObject, cairo

NODE_WIDTH = 128
NODE_HEIGHT = 44
NODE_MARGIN_X = 20
NODE_MARGIN_Y = 20

class TreeNode:
    def __init__(self, value):
        self.value = value
        self.children = []

class StagesTreeView(Gtk.DrawingArea):
    __gtype_name__ = 'StagesTreeView'

    def __init__(self):
        super().__init__()
        self.root_nodes = []
        self.node_positions = {}
        self.set_draw_func(self.draw_func)
        self._width = 0
        self._height = 0

    def set_root_nodes(self, root_nodes: list[TreeNode]):
        print(f"[DEBUG] Setting root nodes: {len(root_nodes)}")
        self.root_nodes = root_nodes
        self._layout_tree()
        self.queue_draw()

    def _is_descendant(self, node, ancestor):
        if node == ancestor:
            return True
        current = node
        while hasattr(current.value, "parent_id") and current.value.parent_id:
            parent_node = next((n for n in self.node_positions if n.value.id == current.value.parent_id), None)
            if parent_node is None:
                break
            if parent_node == ancestor:
                return True
            current = parent_node
        return False

    def draw_func(self, area, context, width, height):
        context.set_line_width(2)
        context.set_source_rgb(0.5, 0.5, 0.5)
        for parent, (px, py, pw, ph) in self.node_positions.items():
            cx, cy = px + pw // 2, py + ph
            for child in parent.children:
                if child in self.node_positions:
                    child_x, child_y, cw, ch = self.node_positions[child]
                    ccx, ccy = child_x + cw // 2, child_y
                    context.move_to(cx, cy)
                    context.line_to(ccx, ccy)
                    context.stroke()
        for node, (x, y, w, h) in self.node_positions.items():
            context.set_source_rgb(0.2, 0.6, 0.8)
            context.rectangle(x, y, w, h)
            context.fill()
            context.set_source_rgb(1, 1, 1)
            context.set_font_size(12)
            label = str(getattr(node.value, "name", "Unnamed"))
            extents = context.text_extents(label)
            text_x = x + (w - extents.width) / 2 - extents.x_bearing
            text_y = y + (h + extents.height) / 2 - extents.y_bearing
            context.move_to(text_x, text_y)
            context.show_text(label)

    def set_content_width(self, width):
        self.set_property("width-request", width)

    def set_content_height(self, height):
        self.set_property("height-request", height)

    def _layout_tree(self):
        def layout_node(node, x=0, y=0):
            if not node.children:
                self.node_positions[node] = (x, y, NODE_WIDTH, NODE_HEIGHT)
                return x, x + NODE_WIDTH

            child_y = y + NODE_HEIGHT + NODE_MARGIN_Y
            children_ranges = []
            current_x = x

            for child in node.children:
                min_x, max_x = layout_node(child, current_x, child_y)
                children_ranges.append((min_x, max_x))
                current_x = max_x + NODE_MARGIN_X

            children_min_x = children_ranges[0][0]
            children_max_x = children_ranges[-1][1]
            children_center_x = (children_min_x + children_max_x) / 2

            node_x = children_center_x - NODE_WIDTH / 2
            self.node_positions[node] = (node_x, y, NODE_WIDTH, NODE_HEIGHT)

            subtree_min_x = min(node_x, children_min_x)
            subtree_max_x = max(node_x + NODE_WIDTH, children_max_x)

            return subtree_min_x, subtree_max_x

        # First pass: calculate widths without stacking vertically
        subtree_sizes = []
        for root in self.root_nodes:
            self.node_positions.clear()
            min_x, max_x = layout_node(root, 0, 0)
            width = max_x - min_x
            subtree_sizes.append((root, width, min_x, max_x))

        if not subtree_sizes:
            return
        max_subtree_width = max(width for _, width, _, _ in subtree_sizes)

        # Second pass: layout all with vertical stacking and horizontal centering
        all_positions = {}
        current_y = 0

        for root, width, min_x, max_x in subtree_sizes:
            self.node_positions.clear()
            layout_node(root, 0, 0)

            # Shift positions so min_x aligns to 0
            shift_x = -min_x
            shifted_positions = {}
            for node, (x, y, w, h) in self.node_positions.items():
                shifted_positions[node] = (x + shift_x, y, w, h)

            # Center subtree horizontally in max_subtree_width
            center_shift = (max_subtree_width - width) / 2
            for node, (x, y, w, h) in shifted_positions.items():
                shifted_positions[node] = (x + center_shift, y + current_y, w, h)

            all_positions.update(shifted_positions)

            # Calculate height of this subtree for stacking
            subtree_height = max(pos[1] + pos[3] for pos in shifted_positions.values()) - current_y
            current_y += subtree_height + NODE_MARGIN_Y

        # Add left margin to all nodes for symmetric horizontal margin
        for node, (x, y, w, h) in all_positions.items():
            all_positions[node] = (x + NODE_MARGIN_X, y, w, h)

        self.node_positions = all_positions

        # Compute final widget size with margins on both sides
        all_x_extents = [x + w for (x, y, w, h) in self.node_positions.values()]
        self._width = (max(all_x_extents) + NODE_MARGIN_X) if all_x_extents else 0
        self._height = current_y

        self.set_content_width(self._width)
        self.set_content_height(self._height)
        self.queue_draw()

