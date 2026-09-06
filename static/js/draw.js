(function () {
  var box = document.getElementById('jxgbox');
  if (!box || typeof JXG === 'undefined') return;

  var board = JXG.JSXGraph.initBoard('jxgbox', {
    renderer: 'canvas',
    boundingbox: [-10, 7, 10, -7],
    axis: false,
    grid: true,
    showNavigation: false,
    showCopyright: false,
    pan: { enabled: false },
    zoom: { enabled: false }
  });

  var mode = 'move';
  var pending = [];
  var undoStack = [];
  var HINTS = {
    move: '移动模式——可直接拖动图形元素',
    point: '点击画布放置一个点',
    segment: '依次点击两个位置（可直接点已有端点）连成线段',
    line: '点击两个点画直线',
    ray: '点击起点和经过点画射线',
    circle: '先点圆心，再点圆上一点画圆',
    polygon: '依次点击各顶点，回到起点或双击结束',
    perp: '先点一个点，再点击目标直线/线段，画垂线',
    parallel: '先点一个点，再点击目标直线/线段，画平行线',
    midpoint: '点击两个点（或线段两端点），生成中点',
    angle: '依次点击：角的一条边上的点、顶点、另一条边上的点',
    text: '点击放置文字标签的位置',
    delete: '点击要删除的元素'
  };

  var INK = '#1f2937';
  var ACCENT = '#4f46e5';

  function coords(e) {
    var i = board.getAttribute('boundingbox');
    var r = board.canvasContainer.getBoundingClientRect();
    var x = i[0] + (e.clientX - r.left) / r.width * (i[2] - i[0]);
    var y = i[1] + (e.clientY - r.top) / r.height * (i[3] - i[1]);
    return [x, y];
  }

  function nearPoint(c) {
    var best = null, bd = 0.6;
    for (var el of board.objectsList) {
      if (el.elType !== 'point' || !el.visProp.visible) continue;
      var d = Math.abs(el.X() - c[0]) + Math.abs(el.Y() - c[1]);
      if (d < bd) { bd = d; best = el; }
    }
    return best;
  }

  function nearLine(c) {
    var best = null, bd = 0.5;
    for (var el of board.objectsList) {
      if (['segment', 'line', 'circle'].indexOf(el.elType) < 0) continue;
      var has = false;
      try { has = el.hasPoint(c[0], c[1], 1.2); } catch (err) { has = false; }
      if (has) { best = el; break; }
    }
    return best;
  }

  function mkPoint(c, label) {
    return board.create('point', c, {
      size: 3, color: ACCENT, strokeColor: ACCENT, fillColor: ACCENT,
      label: { fontSize: 15, color: INK, offset: [8, 8] }, name: label || ''
    });
  }

  function track(el) {
    undoStack.push(el);
    el.on('down', function () {
      if (mode === 'delete') {
        board.removeObject(el);
        var i = undoStack.indexOf(el);
        if (i >= 0) undoStack.splice(i, 1);
      }
    });
  }

  function labelPoints(els) {
    var n = 0;
    for (var el of board.objectsList) if (el.elType === 'point') n++;
  }

  board.on('down', function (e) {
    if (mode === 'move' || mode === 'delete') return;
    var c = coords(e);
    if (mode === 'point') { track(mkPoint(c, nextName())); return; }
    if (mode === 'text') {
      var t = window.prompt('输入标注文字（如 AB=5、∠1=30°）：', '');
      if (t) track(board.create('text', [c[0], c[1], t], { fontSize: 16, color: INK }));
      return;
    }
    if (mode === 'polygon') {
      var np = nearPoint(c);
      if (np && pending.length >= 2 && np === pending[0]) {
        track(board.create('polygon', pending.slice(), {
          borders: { strokeColor: INK, strokeWidth: 2 },
          fillColor: '#eef2ff', fillOpacity: 0.35, vertices: { visible: false }
        }));
        pending = [];
        return;
      }
      pending.push(np || mkPoint(c, nextName()));
      return;
    }
    if (mode === 'perp' || mode === 'parallel') {
      var ln = nearLine(c);
      if (ln) {
        if (!pending.length) { pending.push(ln); }
        else {
          var anchor = pending[0];
          var p = mkPoint(c, nextName());
          if (mode === 'perp') track(board.create('perpendicular', [p, anchor], { strokeColor: '#dc2626', strokeWidth: 2 }));
          else track(board.create('parallel', [p, anchor], { strokeColor: '#dc2626', strokeWidth: 2 }));
          pending = [];
        }
      }
      return;
    }
    if (mode === 'angle') {
      pending.push(nearPoint(c) || mkPoint(c, nextName()));
      if (pending.length === 3) {
        var ang = board.create('angle', [pending[0], pending[1], pending[2]], {
          radius: 0.9, fillColor: '#f59e0b', fillOpacity: 0.5,
          label: { fontSize: 15, color: '#b45309' }
        });
        track(ang);
        pending = [];
      }
      return;
    }
    pending.push(nearPoint(c) || mkPoint(c, nextName()));
    if (pending.length === 2) {
      var a = pending[0], b = pending[1];
      if (mode === 'segment') track(board.create('segment', [a, b], { strokeColor: INK, strokeWidth: 2.5 }));
      if (mode === 'line') track(board.create('line', [a, b], { strokeColor: INK, strokeWidth: 2 }));
      if (mode === 'ray') track(board.create('line', [a, b], { strokeColor: INK, strokeWidth: 2, straightFirst: false, straightSecond: true }));
      if (mode === 'circle') track(board.create('circle', [a, b], { strokeColor: INK, strokeWidth: 2.5 }));
      if (mode === 'midpoint') track(board.create('midpoint', [a, b], { size: 2.5, color: '#dc2626', fillColor: '#dc2626', strokeColor: '#dc2626' }));
      pending = [];
    }
  });

  board.on('up', function () { labelPoints(); });

  var NAMES = 'ABCDEFGHIJKLMNOPQ';
  var nameIdx = 0;
  function nextName() { return NAMES[nameIdx++ % NAMES.length]; }

  var hint = document.getElementById('drawHint');
  document.getElementById('toolbar').addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-mode]');
    if (!btn) return;
    mode = btn.dataset.mode;
    pending = [];
    document.querySelectorAll('#toolbar .tool').forEach(function (b) { b.classList.remove('on'); });
    btn.classList.add('on');
    hint.textContent = '当前：' + HINTS[mode];
  });

  document.getElementById('undoBtn').addEventListener('click', function () {
    var el = undoStack.pop();
    if (el) board.removeObject(el);
  });
  document.getElementById('clearBtn').addEventListener('click', function () {
    if (!window.confirm('清空画布？')) return;
    JXG.JSXGraph.freeBoard(board);
    location.reload();
  });

  function canvasEl() { return box.querySelector('canvas'); }

  document.getElementById('downloadBtn').addEventListener('click', function () {
    var cv = canvasEl();
    if (!cv) return;
    var a = document.createElement('a');
    a.download = 'geometry.png';
    a.href = cv.toDataURL('image/png');
    a.click();
  });

  document.getElementById('attachBtn').addEventListener('click', function () {
    var cv = canvasEl();
    if (!cv) return;
    hint.textContent = '正在保存附图…';
    fetch((window.APP_ROOT || '') + 'save_drawing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: cv.toDataURL('image/png') })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok) {
          location.href = 'mistakes?photo=' + encodeURIComponent(d.photo) + '&drawn=1';
        } else {
          hint.textContent = d.msg || '保存失败，请重试';
        }
      })
      .catch(function () { hint.textContent = '保存失败（网络原因）'; });
  });
})();
