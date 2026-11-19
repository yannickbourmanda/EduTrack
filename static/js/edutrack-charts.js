/**
 * Small Chart.js wrapper for EduTrack.
 *
 * All data passed to these helpers comes from server-rendered database records
 * (via Django's json_script filter). The wrapper only applies consistent labels,
 * colours and accessibility options; it never invents values.
 */
(function () {
  'use strict';

  var INK = '#475569';
  var GRID = '#e8edf3';
  var FONT = '"Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif';
  var REGISTERED = [];

  // Shared series palette (matches static/css/edutrack.css tokens). Templates
  // reference these instead of repeating raw hex literals so every chart
  // stays visually consistent and colour-distinguishable.
  var PALETTE = {
    primary: '#1f5f8b',
    primaryFill: 'rgba(31, 95, 139, 0.08)',
    navy: '#12263f',
    slate: '#3e6180',
    success: '#1f6f54',
    successFill: 'rgba(31, 111, 84, 0.08)',
    warning: '#8a5a12',
    warningFill: 'rgba(138, 90, 18, 0.08)',
    danger: '#a12b2b',
    gold: '#c9a227',
    neutral: '#cbd5e1'
  };

  function baseOptions(yTitle, extra) {
    var options = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: INK,
            usePointStyle: true,
            boxWidth: 8,
            padding: 16,
            font: { family: FONT, size: 11 }
          }
        },
        tooltip: {
          backgroundColor: '#12263f',
          titleFont: { family: FONT, size: 12 },
          bodyFont: { family: FONT, size: 12 },
          padding: 10,
          cornerRadius: 6,
          callbacks: {
            label: function (context) {
              var value = context.parsed.y;
              if (value === null || value === undefined || isNaN(value)) {
                return context.dataset.label + ': no recorded data';
              }
              return context.dataset.label + ': ' + value + '%';
            }
          }
        }
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: {
            color: INK,
            maxRotation: 0,
            autoSkip: true,
            font: { family: FONT, size: 11 }
          }
        },
        y: {
          beginAtZero: true,
          max: 100,
          grid: { color: GRID, drawBorder: false },
          ticks: {
            color: INK,
            stepSize: 20,
            font: { family: FONT, size: 11 },
            callback: function (value) { return value + '%'; }
          },
          title: {
            display: Boolean(yTitle),
            text: yTitle || '',
            color: INK,
            font: { family: FONT, size: 11 }
          }
        }
      }
    };

    if (extra) {
      Object.keys(extra).forEach(function (key) {
        if (key === 'plugins') {
          Object.assign(options.plugins, extra.plugins);
        } else if (key === 'scales') {
          Object.assign(options.scales, extra.scales);
        } else {
          options[key] = extra[key];
        }
      });
    }
    return options;
  }

  function register(chart) {
    if (chart) { REGISTERED.push(chart); }
    return chart;
  }

  function make(canvasId, config) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') { return null; }
    var existing = Chart.getChart(canvas);
    if (existing) { existing.destroy(); }
    return register(new Chart(canvas, config));
  }

  function bar(canvasId, labels, datasets, options) {
    return make(canvasId, {
      type: 'bar',
      data: { labels: labels, datasets: datasets },
      options: baseOptions('Percentage (%)', options)
    });
  }

  function line(canvasId, labels, datasets, options) {
    return make(canvasId, {
      type: 'line',
      data: { labels: labels, datasets: datasets },
      options: baseOptions('Percentage (%)', Object.assign({
        elements: { line: { tension: 0.25, borderWidth: 2 }, point: { radius: 3, hoverRadius: 4 } }
      }, options || {}))
    });
  }

  function doughnut(canvasId, labels, values, colors) {
    return make(canvasId, {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [{
          data: values,
          backgroundColor: colors,
          borderColor: '#ffffff',
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '64%',
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              color: INK,
              usePointStyle: true,
              boxWidth: 8,
              padding: 14,
              font: { family: FONT, size: 11 }
            }
          },
          tooltip: {
            backgroundColor: '#12263f',
            padding: 10,
            cornerRadius: 6,
            callbacks: {
              label: function (context) {
                return context.label + ': ' + context.parsed + ' student(s)';
              }
            }
          }
        }
      }
    });
  }

  function destroyAll() {
    REGISTERED.forEach(function (chart) {
      try { chart.destroy(); } catch (error) { /* no-op */ }
    });
    REGISTERED = [];
  }

  window.EduTrackCharts = {
    make: make,
    bar: bar,
    line: line,
    doughnut: doughnut,
    destroyAll: destroyAll,
    palette: PALETTE
  };
})();
