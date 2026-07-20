/* ============================================================
   Chart.js Wrappers — dark-themed chart helpers
   ============================================================ */

window.Charts = (() => {
  const _instances = {};

  function getThemeColor(varName, fallback) {
    const val = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    return val || fallback;
  }

  // Global Chart.js defaults — reads CSS variables for theme awareness
  function applyDefaults() {
    if (!window.Chart) return;
    const textSecondary = getThemeColor('--text-secondary', '#cbd5e1');
    const textPrimary = getThemeColor('--text-primary', '#ffffff');
    const borderColor = getThemeColor('--border', 'rgba(255,255,255,0.08)');
    const bgSecondary = getThemeColor('--bg-secondary', '#071330');
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const gridColor = isLight ? 'rgba(0, 0, 0, 0.06)' : 'rgba(255, 255, 255, 0.06)';

    Chart.defaults.color = textSecondary;
    Chart.defaults.borderColor = borderColor;
    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.pointStyle = 'circle';
    Chart.defaults.plugins.legend.labels.padding = 16;
    Chart.defaults.plugins.legend.labels.color = textSecondary;
    Chart.defaults.plugins.tooltip.backgroundColor = bgSecondary;
    Chart.defaults.plugins.tooltip.titleColor = textPrimary;
    Chart.defaults.plugins.tooltip.bodyColor = textSecondary;
    Chart.defaults.plugins.tooltip.borderColor = borderColor;
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
    gradient.addColorStop(0, 'rgba(34, 197, 94, 0.85)');
    gradient.addColorStop(1, 'rgba(34, 197, 94, 0.05)');

    const chart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: options.label || 'Số file',
          data,
          backgroundColor: gradient,
          borderColor: 'rgba(34, 197, 94, 0.95)',
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
              color: document.documentElement.getAttribute('data-theme') === 'light' ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)',
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
      '#22c55e', '#f59e0b', '#c084fc', '#3b82f6', '#ef4444',
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
          borderColor: getThemeColor('--bg-secondary', '#071330'),
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
                      pointStyle: 'circle',
                      fontColor: getThemeColor('--text-secondary', '#cbd5e1')
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
            grid: { color: document.documentElement.getAttribute('data-theme') === 'light' ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)', drawBorder: false },
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

  function createStackedBarChart(canvasId, labels, successData, failedData, options = {}) {
    destroy(canvasId);
    const ctx = getCanvas(canvasId);
    if (!ctx) return null;

    const successGradient = ctx.createLinearGradient(0, 0, 0, 300);
    successGradient.addColorStop(0, 'rgba(34, 197, 94, 0.85)');
    successGradient.addColorStop(1, 'rgba(34, 197, 94, 0.15)');

    const failedGradient = ctx.createLinearGradient(0, 0, 0, 300);
    failedGradient.addColorStop(0, 'rgba(239, 68, 68, 0.85)');
    failedGradient.addColorStop(1, 'rgba(239, 68, 68, 0.15)');

    const chart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: options.successLabel || 'Thành công',
            data: successData,
            backgroundColor: successGradient,
            borderColor: 'rgba(34, 197, 94, 0.95)',
            borderWidth: 1,
            borderRadius: 6,
            borderSkipped: false,
            barPercentage: 0.6,
            categoryPercentage: 0.7,
          },
          {
            label: options.failedLabel || 'Thất bại',
            data: failedData,
            backgroundColor: failedGradient,
            borderColor: 'rgba(239, 68, 68, 0.95)',
            borderWidth: 1,
            borderRadius: 6,
            borderSkipped: false,
            barPercentage: 0.6,
            categoryPercentage: 0.7,
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 800, easing: 'easeOutQuart' },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: { usePointStyle: true, pointStyle: 'circle', padding: 16 }
          },
          ...options.plugins
        },
        scales: {
          x: {
            stacked: true,
            grid: { display: false },
            ticks: { maxRotation: 45 }
          },
          y: {
            stacked: true,
            beginAtZero: true,
            grid: {
              color: document.documentElement.getAttribute('data-theme') === 'light'
                ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)',
              drawBorder: false
            },
            ticks: { precision: 0 }
          }
        },
        ...options.chartOptions
      }
    });

    _instances[canvasId] = chart;
    return chart;
  }

  function applyThemeColors() {
    applyDefaults();
    Object.keys(_instances).forEach(id => {
      const chart = _instances[id];
      if (!chart) return;
      // Update doughnut border colors
      if (chart.config.type === 'doughnut') {
        chart.data.datasets.forEach(ds => {
          ds.borderColor = getThemeColor('--bg-secondary', '#071330');
        });
      }
      // Update grid colors for bar/line charts
      const isLight = document.documentElement.getAttribute('data-theme') === 'light';
      const gridColor = isLight ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)';
      if (chart.options.scales) {
        Object.values(chart.options.scales).forEach(scale => {
          if (scale.grid) scale.grid.color = gridColor;
        });
      }
      // Update legend label colors
      if (chart.config.type === 'doughnut' && chart.options.plugins?.legend?.labels?.generateLabels) {
        // generateLabels will re-read getThemeColor on next render
      }
      chart.update('none');
    });
  }

  return { createBarChart, createStackedBarChart, createDoughnutChart, createLineChart, destroy, applyThemeColors };
})();
