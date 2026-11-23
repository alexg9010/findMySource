% Smooth the sensor signal and plot it.
M = readmatrix('data/raw/signal.csv');
S = movmean(M, 5);
writematrix(S, 'results/stats/signal_smooth.csv');
plot(S');
saveas(gcf, 'figures/qc/signal.png');
