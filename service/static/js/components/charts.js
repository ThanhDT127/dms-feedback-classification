/* ============================================================
   Chart.js Wrappers — dark-themed chart helpers
   ============================================================ */

window.Charts = (() => {
  const _instances = {};

  // Global Chart.js defaults for dark theme
  function applyDefaults() {
    if (!window.Chart) return;
    Chart.defaults.color = '#8b8fa3';
    Chart.defaults.borderColor = 'rgba(45, 49, 72, 0.5)';
    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.pointStyle = 'circle';
    Chart.defaults.plugins.legend.labels.padding = 16;
    Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(26, 29, 40, 0.95)';
    Chart.defaults.plugins.tooltip.titleColor = '#e4e6ed';
    Chart.defaults.plugins.tooltip.bodyColor = '#8b8fa3';
    Chart.defaults.plugins.tooltip.borderColor = '#2d3148';
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.displayColors = true;
    Chart.defaults.plugins.tooltip.boxPadding = 4;
  }

  function destroy(canvasId) {
    if (_instances[canvasId]) {
      _instances[canvasId].destroy();
      delete _instances[canvasId];
    }
  }

  function getCanvas(canvasId) {
    const el = document.getElementById(canvasId);
    if (!el) return null;
    return el.getContext('2d');
  }

  function createBarChart(canvasId, labels, data, options = {}) {
    destroy(canvasId);
    const ctx = getCanvas(canvasId);
    if (!ctx) return null;

    const gradient = ctx.createLinearGradient(0, 0, 0, 300);
    gradient.addColorStop(0, 'rgba(59, 130, 246, 0.8)');
    gradient.addColorStop(1, 'rgba(59, 130, 246, 0.1)');

    const chart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: options.label || 'Số file',
          data,
          backgroundColor: gradient,
          borderColor: 'rgba(59, 130, 246, 0.9)',
          borderWidth: 1,
          borderRadius: 6,
          borderSkipped: false,
          barPercentage: 0.6,
          categoryPercentage: 0.7,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: {
          duration: 800,
          easing: 'easeOutQuart'
        },
        plugins: {
          legend: { display: false },
          ...options.plugins
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxRotation: 45 }
          },
          y: {
            beginAtZero: true,
            grid: {
              color: 'rgba(45, 49, 72, 0.3)',
              drawBorder: false
            },
            ticks: {
              stepSize: options.stepSize || undefined,
              precision: 0
            }
          }
        },
        ...options.chartOptions
      }
    });

    _instances[canvasId] = chart;
    return chart;
  }

  function createDoughnutChart(canvasId, labels, data, colors = null) {
    destroy(canvasId);
    const ctx = getCanvas(canvasId);
    if (!ctx) return null;

    const defaultColors = [
      '#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#a855f7',
      '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1',
      '#14b8a6', '#e11d48', '#eab308', '#8b5cf6', '#10b981',
      '#f43f5e', '#0ea5e9', '#d946ef', '#fbbf24', '#64748b'
    ];

    const bgColors = colors || defaultColors.slice(0, labels.length);

    const chart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels,
        datasets: [{
          data,
          backgroundColor: bgColors,
          borderColor: '#1a1d28',
          borderWidth: 2,
          hoverOffset: 8,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '65%',
        animation: {
          animateRotate: true,
          duration: 1000,
          easing: 'easeOutQuart'
        },
        plugins: {
          legend: {
            position: 'right',
            labels: {
              padding: 12,
              font: { size: 11 },
              generateLabels: function(chart) {
                const data = chart.data;
                if (data.labels.length && data.datasets.length) {
                  return data.labels.map((label, i) => {
                    const value = data.datasets[0].data[i];
                    return {
                      text: `${label} (${value})`,
                      fillStyle: data.datasets[0].backgroundColor[i],
                      strokeStyle: data.datasets[0].backgroundColor[i],
                      lineWidth: 0,
                      hidden: false,
                      index: i,
                      pointStyle: 'circle'
                    };
                  });
                }
                return [];
              }
            }
          }
        }
      }
    });

    _instances[canvasId] = chart;
    return chart;
  }

  function createLineChart(canvasId, labels, datasets, options = {}) {
    destroy(canvasId);
    const ctx = getCanvas(canvasId);
    if (!ctx) return null;

    const chart = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 800, easing: 'easeOutQuart' },
        interaction: { intersect: false, mode: 'index' },
        plugins: {
          legend: { display: datasets.length > 1 },
          ...options.plugins
        },
        scales: {
          x: { grid: { display: false } },
          y: {
            beginAtZero: true,
            grid: { color: 'rgba(45,49,72,0.3)', drawBorder: false },
            ticks: { precision: 0 }
          }
        },
        elements: {
          point: { radius: 3, hoverRadius: 6 },
          line: { tension: 0.3, borderWidth: 2 }
        },
        ...options.chartOptions
      }
    });

    _instances[canvasId] = chart;
    return chart;
  }

  // Initialize defaults when ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyDefaults);
  } else {
    applyDefaults();
  }

  return { createBarChart, createDoughnutChart, createLineChart, destroy };
})();
