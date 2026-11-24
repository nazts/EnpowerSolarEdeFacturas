/* Editor de posición de una firma o sello sobre la hoja de la cotización.
 *
 * Lo usan dos pantallas: "Firmas y sellos" (posición por defecto de cada firma)
 * y "Firmar cotización" (posición sólo para esa cotización).
 *
 * Coordenadas: la hoja se mide en puntos del PDF (612 x 791). En pantalla la
 * hoja se dibuja al ancho que dé el CSS, así que todo se convierte con la
 * escala `anchoPantalla / anchoHoja`. Ojo con la Y: en el PDF crece hacia
 * arriba y en el navegador hacia abajo, y `y` es la BASE de la imagen.
 */
(function () {
  var LINEA_FIRMA = 130;   // y de la línea donde se firma

  function crear(opciones) {
    var hoja = opciones.hoja;
    var sello = opciones.sello;
    var anchoHoja = opciones.anchoHoja;
    var altoHoja = opciones.altoHoja;
    var alCambiar = opciones.alCambiar || function () {};

    var ancho = Number(opciones.ancho) || 160;
    var x = Number(opciones.x);
    var y = Number(opciones.y);
    if (!isFinite(x)) x = 60;
    if (!isFinite(y)) y = LINEA_FIRMA;
    var proporcion = 0.35;                 // alto / ancho; se corrige al cargar
    var arrastrando = false;
    var inicio = null;

    function escala() { return hoja.clientWidth / anchoHoja; }
    function altoPt() { return ancho * proporcion; }

    function pintar() {
      var k = escala();
      sello.style.width = (ancho * k) + 'px';
      sello.style.left = (x * k) + 'px';
      sello.style.top = ((altoHoja - y - altoPt()) * k) + 'px';
      alCambiar({x: x, y: y, ancho: ancho});
    }

    function medir() {
      if (sello.naturalWidth) proporcion = sello.naturalHeight / sello.naturalWidth;
      pintar();
    }

    function limitar() {
      x = Math.min(Math.max(x, 8), anchoHoja - ancho - 8);
      y = Math.min(Math.max(y, 8), altoHoja - altoPt() - 8);
      if (Math.abs(y - LINEA_FIRMA) < 14) y = LINEA_FIRMA;   // imán sobre la línea
    }

    sello.addEventListener('pointerdown', function (ev) {
      ev.preventDefault();
      arrastrando = true;
      sello.classList.add('arrastrando');
      try { sello.setPointerCapture(ev.pointerId); } catch (e) { /* sin captura */ }
      inicio = {x: ev.clientX, y: ev.clientY, x0: x, y0: y};
    });
    sello.addEventListener('pointermove', function (ev) {
      if (!arrastrando) return;
      var k = escala();
      x = inicio.x0 + (ev.clientX - inicio.x) / k;
      y = inicio.y0 - (ev.clientY - inicio.y) / k;
      limitar();
      pintar();
    });
    function soltar() {
      arrastrando = false;
      sello.classList.remove('arrastrando');
    }
    sello.addEventListener('pointerup', soltar);
    sello.addEventListener('pointercancel', soltar);

    window.addEventListener('resize', pintar);
    sello.addEventListener('load', medir);
    sello.addEventListener('error', medir);
    if (sello.complete) medir();
    setTimeout(pintar, 0);      // aunque la imagen todavía no haya cargado

    return {
      posicion: function () { return {x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10,
                                      ancho: Math.round(ancho * 10) / 10}; },
      tamano: function (nuevo) {
        if (nuevo) { ancho = Number(nuevo) || ancho; limitar(); pintar(); }
        return ancho;
      },
      refrescar: pintar
    };
  }

  window.EditorFirma = {crear: crear, linea: LINEA_FIRMA};
})();
