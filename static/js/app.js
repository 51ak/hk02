(function () {
  var photo = document.getElementById('photoInput');
  var title = document.getElementById('titleInput');
  var mine = document.getElementById('mineInput');
  var corr = document.getElementById('corrInput');
  var status = document.getElementById('ocrStatus');
  var preview = document.getElementById('photoPreview');
  var photoName = document.getElementById('photoName');
  if (photoName && photoName.value && preview) {
    preview.src = 'photo/' + photoName.value;
    preview.style.display = '';
  }
  if (photo && status) {
    photo.addEventListener('change', function () {
      var f = photo.files && photo.files[0];
      if (!f) return;
      if (f.size > 15 * 1024 * 1024) {
        status.textContent = '图片超过 15MB，请压缩后重试';
        return;
      }
      if (preview) {
        preview.src = URL.createObjectURL(f);
        preview.style.display = '';
      }
      status.textContent = '正在分区识别（印刷原题 / 手写作答 / 红笔订正）… 约 10~30 秒';
      var fd = new FormData();
      fd.append('photo', f);
      fetch('ocr', { method: 'POST', body: fd })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.photo && photoName) photoName.value = d.photo;
          if (d.ok) {
            if (d.original && title) title.value = d.original;
            if (d.mine && mine) mine.value = d.mine;
            if (d.correction && corr) corr.value = d.correction;
            status.textContent = d.msg || '识别完成，请核对三个框的内容并修正；原图已保存';
          } else {
            status.textContent = d.msg || '识别失败，请手动输入题干';
          }
        })
        .catch(function () {
          status.textContent = '上传失败（网络原因），可手动输入题干';
        });
    });
  }
  var cause = document.getElementById('causeSelect');
  var logic = document.getElementById('logicWrap');
  var logicSel = document.getElementById('logicSelect');
  function toggle() {
    if (!cause || !logic) return;
    if (cause.value === '逻辑思维') {
      logic.style.display = '';
    } else {
      logic.style.display = 'none';
      if (logicSel) logicSel.value = '';
    }
  }
  if (cause) {
    cause.addEventListener('change', toggle);
    toggle();
  }
})();
