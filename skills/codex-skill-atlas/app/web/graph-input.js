'use strict';

// Screen-space input is kept separate from rendering and live graph updates.
class GraphInput {
  constructor(options) {
    this.options = options;
    this.spaceHeld = false;
    this.gesture = null;
  }

  cursor(hovered = false) {
    const mode = this.gesture?.mode;
    return this.spaceHeld ? (mode === 'pan' ? 'grabbing' : 'grab') :
      mode === 'box' && this.gesture.moved ? 'crosshair' :
      mode === 'nodes' && this.gesture.moved ? 'move' : hovered ? 'pointer' : 'default';
  }

  setSpace(held) {
    if (this.spaceHeld === held) return;
    this.spaceHeld = held;
    const g = this.gesture;
    if (!held && g?.mode === 'pan') this.finish();
    else if (held && g && g.mode !== 'pan') {
      if (g.mode === 'box') this.options.setSelection(new Set(g.previousSelection));
      this.options.showBox(null);
      this.gesture = {mode:'pan', pointerId:g.pointerId, start:g.current, current:g.current,
        pan:{...this.options.getView().pan}, moved:false};
    }
    this.options.updateCursor();
  }

  down(point, node, {pointerId, button = 0, additive = false, dragNode = node} = {}) {
    if (button !== 0 || this.gesture) return false;
    const view = this.options.getView();
    const selected = new Set(this.options.getSelection());
    // A generous click target must not steal a marquee started outside a circle.
    // Shift explicitly starts an additive marquee, including from a node itself.
    const mode = this.spaceHeld ? 'pan' : dragNode && !additive ? 'nodes' : 'box';
    const nodes = mode === 'nodes' ? (selected.has(node.id) ?
      this.options.getNodes().filter(n => selected.has(n.id)) : [node]) : [];
    // Keep the geometry seen at pointerdown stable throughout the whole gesture.
    const boxNodes = mode === 'box' ? this.options.getNodes().map(n => ({id:n.id,
      ...(this.options.getNodeGeometry?.(n) ||
        {x:n.x * view.scale + view.pan.x, y:n.y * view.scale + view.pan.y, radius:(n.r || 0) * view.scale})})) : [];
    this.gesture = {mode, pointerId, node, start:point, current:point, moved:false,
      pan:{...view.pan}, scale:view.scale, additive, previousSelection:selected,
      boxNodes, positions:nodes.map(n => ({id:n.id, x:n.x, y:n.y}))};
    this.options.updateCursor();
    return true;
  }

  move(point, pointerId) {
    const g = this.gesture;
    if (!g || pointerId !== g.pointerId) return;
    g.current = point;
    const dx = point.x - g.start.x, dy = point.y - g.start.y;
    if (Math.hypot(dx, dy) > 4) g.moved = true;
    if (!g.moved) return;
    if (g.mode === 'pan') this.options.setPan({x:g.pan.x + dx, y:g.pan.y + dy});
    else if (g.mode === 'nodes') {
      if (!g.previousSelection.has(g.node.id)) this.options.setSelection(new Set());
      this.options.moveNodes(g.positions.map(n => ({id:n.id, x:n.x + dx / g.scale, y:n.y + dy / g.scale})));
    } else {
      const box = {x:Math.min(g.start.x, point.x), y:Math.min(g.start.y, point.y),
        width:Math.abs(dx), height:Math.abs(dy)};
      const selected = new Set(g.additive ? g.previousSelection : []);
      for (const n of g.boxNodes) {
        const closestX = Math.max(box.x, Math.min(n.x, box.x + box.width));
        const closestY = Math.max(box.y, Math.min(n.y, box.y + box.height));
        if (Math.hypot(n.x - closestX, n.y - closestY) <= n.radius) selected.add(n.id);
      }
      this.options.setSelection(selected);
      this.options.showBox(box);
    }
    this.options.updateCursor();
  }

  up(point, pointerId) {
    if (!this.gesture || pointerId !== this.gesture.pointerId) return;
    this.move(point, pointerId);
    const g = this.gesture;
    // Finish capture before opening details, which can resize the canvas.
    this.finish();
    if (g.mode === 'box' && g.moved) this.options.onSelectBox();
    else if (g.mode !== 'pan' && !g.moved) {
      if (g.node) this.options.onClickNode(g.node);
      else this.options.onClickBlank();
    }
  }

  finish() {
    const g = this.gesture;
    this.gesture = null;
    this.options.showBox(null);
    if (g) this.options.onFinish(g.pointerId);
    this.options.updateCursor();
  }

  cancel({releaseSpace = true} = {}) {
    if (this.gesture?.mode === 'box') this.options.setSelection(new Set(this.gesture.previousSelection));
    if (releaseSpace) this.spaceHeld = false;
    this.finish();
  }
}

if (typeof module !== 'undefined') module.exports = {GraphInput};
