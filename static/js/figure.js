(function (global) {
  function bbox(fig) {
    if (fig.bbox && fig.bbox.length === 4) return fig.bbox;
    var xs = [], ys = [];
    Object.keys(fig.points).forEach(function (k) {
      xs.push(fig.points[k][0]); ys.push(fig.points[k][1]);
    });
    (fig.elements || []).forEach(function (el) {
      if (el.t === 'text' && el.at) { xs.push(el.at[0]); ys.push(el.at[1]); }
    });
    var xmin = Math.min.apply(null, xs), xmax = Math.max.apply(null, xs);
    var ymin = Math.min.apply(null, ys), ymax = Math.max.apply(null, ys);
    var mx = Math.max((xmax - xmin) * 0.15, 1), my = Math.max((ymax - ymin) * 0.15, 1);
    return [xmin - mx, ymax + my, xmax + mx, ymin - my];
  }

  function renderFigure(id, fig) {
    var el = document.getElementById(id);
    if (!el || !fig || typeof JXG === 'undefined') return;
    var board = JXG.JSXGraph.initBoard(id, {
      boundingbox: bbox(fig),
      axis: false, grid: false,
      showNavigation: false, showCopyright: false,
      pan: { enabled: false }, zoom: { enabled: false }
    });
    var pts = {};
    Object.keys(fig.points).forEach(function (name) {
      var c = fig.points[name];
      pts[name] = board.create('point', c, {
        size: 3.2, strokeColor: '#6d5ae0', fillColor: '#9a7bff', highlightFillColor: '#e4d9f8',
        label: { fontSize: 15, color: '#2b2540', offset: [9, 9] }, name: name
      });
    });
    (fig.elements || []).forEach(function (it) {
      try {
        if (it.t === 'polygon') {
          board.create('polygon', it.pts.map(function (p) { return pts[p]; }), {
            borders: { strokeColor: '#2b2540', strokeWidth: 2.2 },
            fillColor: '#eef2ff', fillOpacity: 0.5, vertices: { visible: false }, highlight: false
          });
        } else if (it.t === 'segment') {
          board.create('segment', it.pts.map(function (p) { return pts[p]; }),
            { strokeColor: '#2b2540', strokeWidth: 2.2, highlight: false });
        } else if (it.t === 'line') {
          board.create('line', it.pts.map(function (p) { return pts[p]; }),
            { strokeColor: '#7d7594', strokeWidth: 1.6, highlight: false });
        } else if (it.t === 'ray') {
          board.create('line', it.pts.map(function (p) { return pts[p]; }),
            { strokeColor: '#7d7594', strokeWidth: 1.6, straightFirst: false, straightSecond: true, highlight: false });
        } else if (it.t === 'circle') {
          board.create('circle', [pts[it.c], pts[it.p]],
            { strokeColor: '#2b2540', strokeWidth: 2, highlight: false });
        } else if (it.t === 'angle') {
          var a = board.create('angle', it.pts.map(function (p) { return pts[p]; }), {
            radius: 0.7, fillColor: '#f5b45e', fillOpacity: 0.55, highlight: false
          });
          if (it.label) { a.label.setText(it.label); a.label.setAttribute({ fontSize: 13, color: '#96601a' }); }
        } else if (it.t === 'right') {
          var A = pts[it.pts[0]], B = pts[it.pts[1]], C = pts[it.pts[2]];
          var s = 0.45;
          var ux = (A.X() - B.X()), uy = (A.Y() - B.Y());
          var vx = (C.X() - B.X()), vy = (C.Y() - B.Y());
          var lu = Math.sqrt(ux * ux + uy * uy) || 1, lv = Math.sqrt(vx * vx + vy * vy) || 1;
          ux = ux / lu * s; uy = uy / lu * s; vx = vx / lv * s; vy = vy / lv * s;
          var p1 = [B.X() + ux, B.Y() + uy];
          var p2 = [B.X() + ux + vx, B.Y() + uy + vy];
          var p3 = [B.X() + vx, B.Y() + vy];
          var g = board.create('curve', [[p1[0], p2[0], p3[0]], [p1[1], p2[1], p3[1]]],
            { strokeColor: '#2b2540', strokeWidth: 1.4, highlight: false });
          g.updateCurve = (function (orig) { return function () {
            orig.apply(this, arguments);
          }; })(g.updateCurve);
        } else if (it.t === 'tick') {
          var a1 = pts[it.pts[0]], b1 = pts[it.pts[1]];
          var n = it.n || 1;
          var dx = b1.X() - a1.X(), dy = b1.Y() - a1.Y();
          var L = Math.sqrt(dx * dx + dy * dy) || 1;
          var px = -dy / L * 0.14, py = dx / L * 0.14;
          var mx = (a1.X() + b1.X()) / 2, my2 = (a1.Y() + b1.Y()) / 2;
          for (var i = 0; i < n; i++) {
            var off = (i - (n - 1) / 2) * 0.22;
            var ox = dx / L * off, oy = dy / L * off;
            board.create('segment', [[mx + ox - px, my2 + oy - py], [mx + ox + px, my2 + oy + py]],
              { strokeColor: '#2b2540', strokeWidth: 1.8, highlight: false, fixed: true });
          }
        } else if (it.t === 'text') {
          board.create('text', it.at, [it.s], { fontSize: 14, color: '#d95c5c', highlight: false });
        }
      } catch (e) { }
    });
  }

  global.renderFigure = renderFigure;
})(window);
