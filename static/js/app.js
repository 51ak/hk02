(function () {
  var photo = document.getElementById('photoInput');
  var title = document.getElementById('titleInput');
  var status = document.getElementById('ocrStatus');
  if (photo && title && status) {
    photo.addEventListener('change', function () {
      var f = photo.files && photo.files[0];
      if (!f) return;
      if (f.size > 15 * 1024 * 1024) {
        status.textContent = '图片超过 15MB，请压缩后重试';
        return;
      }
      status.textContent = '正在识别手写内容…';
      var fd = new FormData();
      fd.append('photo', f);
      fetch('ocr', { method: 'POST', body: fd })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.ok) {
            if (title.value.trim()) {
              title.value = title.value.trim() + '\n' + d.text;
            } else {
              title.value = d.text;
            }
            status.textContent = '识别完成，请核对并修正识别文字；原图已随表单保存';
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
