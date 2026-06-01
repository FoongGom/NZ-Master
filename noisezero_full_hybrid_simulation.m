
%% noisezero_full_hybrid_simulation.m
% NoiseZero 전체 하이브리드 층간소음 제어 MATLAB 변환 버전
% 원본 Python 코드의 핵심 구조:
% - 5가지 가상 층간소음 생성
% - 소음 유형 분류
% - Fixed Gain/Delay
% - FxNLMS Adaptive
% - Ringdown Impact Control
% - 하이브리드 선택
%
% 실행:
% MATLAB 명령창에서 noisezero_full_hybrid_simulation 입력

clear; clc; close all;

fs = 1000;
duration = 8;
t = 0:1/fs:duration-1/fs;
rng(10);

SHOW_GRAPHS = true;
TRAIN_RATIO = 0.7;
train_end = floor(length(t)*TRAIN_RATIO);
test_start = train_end + 1;

secondary_actual = make_secondary_path(8,64);
secondary_estimated = make_secondary_path(7,64);

experiments = {};

[child_signal, child_events] = generate_child_running_noise(t, fs, duration);
experiments{end+1} = run_experiment("Child Running Noise", "아기 뛰는 소리", child_signal, round(child_events,3), 150, SHOW_GRAPHS, fs, t, train_end, test_start, secondary_actual, secondary_estimated);

[adult_signal, adult_events] = generate_adult_heavy_footstep_noise(t, fs, duration);
experiments{end+1} = run_experiment("Adult Heavy Footstep Noise", "성인 발망치", adult_signal, round(adult_events,3), 120, SHOW_GRAPHS, fs, t, train_end, test_start, secondary_actual, secondary_estimated);

[washing_signal, washing_events] = generate_washing_machine_noise(t, fs, duration);
experiments{end+1} = run_experiment("Washing Machine Vibration", "세탁기 진동", washing_signal, "continuous vibration", 180, SHOW_GRAPHS, fs, t, train_end, test_start, secondary_actual, secondary_estimated);

[chair_signal, chair_events] = generate_chair_dragging_noise(t, fs, duration);
experiments{end+1} = run_experiment("Chair Dragging Noise", "의자 끄는 소리", chair_signal, chair_events, 200, SHOW_GRAPHS, fs, t, train_end, test_start, secondary_actual, secondary_estimated);

[drop_signal, drop_events] = generate_object_drop_noise(t, fs, duration);
experiments{end+1} = run_experiment("Object Drop Impact Noise", "물건 낙하 충격음", drop_signal, drop_events, 120, SHOW_GRAPHS, fs, t, train_end, test_start, secondary_actual, secondary_estimated);

fprintf("\n\n");
fprintf("================================================================================================================================================\n");
fprintf("전체 실험 결과 요약 - 소음 유형별 하이브리드 제어\n");
fprintf("================================================================================================================================================\n");
fprintf("%-36s | %-24s | %9s | %9s | %8s | %10s | %-25s | %8s | %-20s | %8s\n", ...
    "소음 종류","분류","Fixed dB","FxNLMS dB","Ring dB","Ring잔향","선택 방식","선택 dB","최고 방식","최고 dB");
fprintf("------------------------------------------------------------------------------------------------------------------------------------------------\n");

names = strings(1,length(experiments));
fixed_values = zeros(1,length(experiments));
fx_values = zeros(1,length(experiments));
ring_values = zeros(1,length(experiments));
selected_values = zeros(1,length(experiments));

for i=1:length(experiments)
    r=experiments{i};
    names(i)=sprintf("%s\n(%s)", r.name, r.name_kr);
    fixed_values(i)=r.fixed_db;
    fx_values(i)=r.fxnlms_db;
    ring_values(i)=r.ringdown_db;
    selected_values(i)=r.selected_db;
    fprintf("%-36s | %-24s | %9.3f | %9.3f | %8.3f | %10.3f | %-25s | %8.3f | %-20s | %8.3f\n", ...
        char(r.name + " (" + r.name_kr + ")"), ...
        char(r.noise_type + " (" + r.noise_type_kr + ")"), ...
        r.fixed_db, r.fxnlms_db, r.ringdown_db, r.ringdown_only_db, ...
        char(r.selected_mode + " (" + r.selected_mode_kr + ")"), ...
        r.selected_db, char(r.best_name), r.best_db);
end
fprintf("================================================================================================================================================\n");

figure('Name','NoiseZero Full Hybrid Result');
x=1:length(experiments); width=0.18;
bar(x-1.5*width,fixed_values,width); hold on;
bar(x-0.5*width,fx_values,width);
bar(x+0.5*width,ring_values,width);
bar(x+1.5*width,selected_values,width);
xticks(x); xticklabels(cellstr(names)); xtickangle(20);
ylabel("Reduction [dB] (감소량 [dB])");
title("Hybrid Control Result by Noise Type (소음 유형별 하이브리드 제어 결과)");
legend("Fixed Gain/Delay","FxNLMS Adaptive","Ringdown","Selected Hybrid",'Location','best');
grid on;

%% ========================= 함수 정의 =========================

function y=lowpass_filter(x,cutoff,fs,order)
if nargin<4, order=4; end
[b,a]=butter(order, cutoff/(fs/2), "low");
y=filter(b,a,x);
end

function y=delay_signal(x,delay_samples)
if delay_samples<=0, y=x; return; end
y=zeros(size(x)); y(delay_samples+1:end)=x(1:end-delay_samples);
end

function v=rms_value(x), v=sqrt(mean(x.^2)); end
function v=peak_abs(x), v=max(abs(x)); end

function db=db_reduction(before_signal,after_signal)
br=rms_value(before_signal); ar=rms_value(after_signal);
if ar==0, db=999; else, db=20*log10(br/ar); end
end

function db=peak_db_reduction(before_signal,after_signal)
bp=peak_abs(before_signal); ap=peak_abs(after_signal);
if ap==0, db=999; else, db=20*log10(bp/ap); end
end

function y=apply_secondary_path(x,secondary_path)
temp=conv(x,secondary_path,"full"); y=temp(1:length(x));
end

function s=make_secondary_path(delay_samples,len)
s=zeros(1,len); idx=delay_samples+1;
if idx<=len, s(idx)=0.75; end
if idx+1<=len, s(idx+1)=0.25; end
if idx+2<=len, s(idx+2)=-0.10; end
if idx+3<=len, s(idx+3)=0.05; end
total=sum(abs(s)); if total>0, s=s/total; end
end

function [signal,footstep_times]=generate_child_running_noise(t,fs,duration)
signal=zeros(size(t)); footstep_times=[]; current_time=0.4;
while current_time<duration-0.5
    interval=0.25+(0.45-0.25)*rand(); current_time=current_time+interval;
    footstep_times(end+1)=current_time; %#ok<AGROW>
end
for k=1:length(footstep_times)
    index=floor(footstep_times(k)*fs)+1; strength=0.8+(1.5-0.8)*rand();
    burst_len=floor(0.25*fs);
    if index+burst_len-1>length(signal), burst_len=length(signal)-index+1; end
    if burst_len<=0, continue; end
    bt=(0:burst_len-1)/fs; env=exp(-18*bt);
    burst=strength*env.*(sin(2*pi*30*bt)+0.8*sin(2*pi*55*bt)+0.4*sin(2*pi*90*bt));
    sharp=zeros(1,burst_len); sl=min(20,burst_len); sharp(1:sl)=strength*1.8*exp(-linspace(0,4,sl));
    signal(index:index+burst_len-1)=signal(index:index+burst_len-1)+burst+sharp;
end
signal=signal+0.12*sin(2*pi*25*t)+0.08*sin(2*pi*45*t)+0.05*randn(size(t));
end

function [signal,footstep_times]=generate_adult_heavy_footstep_noise(t,fs,duration)
signal=zeros(size(t)); footstep_times=[]; current_time=0.6;
while current_time<duration-0.5
    interval=0.55+(0.85-0.55)*rand(); current_time=current_time+interval;
    footstep_times(end+1)=current_time; %#ok<AGROW>
end
for k=1:length(footstep_times)
    index=floor(footstep_times(k)*fs)+1; strength=1.3+(2.2-1.3)*rand();
    burst_len=floor(0.35*fs);
    if index+burst_len-1>length(signal), burst_len=length(signal)-index+1; end
    if burst_len<=0, continue; end
    bt=(0:burst_len-1)/fs; env=exp(-10*bt);
    burst=strength*env.*(sin(2*pi*20*bt)+0.9*sin(2*pi*35*bt)+0.5*sin(2*pi*60*bt));
    sharp=zeros(1,burst_len); sl=min(25,burst_len); sharp(1:sl)=strength*2.2*exp(-linspace(0,5,sl));
    signal(index:index+burst_len-1)=signal(index:index+burst_len-1)+burst+sharp;
end
signal=signal+0.08*sin(2*pi*30*t)+0.04*randn(size(t));
end

function [signal,events]=generate_washing_machine_noise(t,fs,duration)
%#ok<INUSD>
signal=0.8*sin(2*pi*45*t)+0.5*sin(2*pi*90*t)+0.25*sin(2*pi*135*t);
signal=signal.*(1.0+0.2*sin(2*pi*0.5*t))+0.04*randn(size(t));
events=[];
end

function [signal,drag_sections]=generate_chair_dragging_noise(t,fs,duration)
%#ok<INUSD>
signal=zeros(size(t)); drag_sections=[1.0 2.0; 3.0 3.8; 5.2 6.4];
for k=1:size(drag_sections,1)
    si=floor(drag_sections(k,1)*fs)+1; ei=floor(drag_sections(k,2)*fs);
    len=ei-si+1; if len<=0, continue; end
    dt=(0:len-1)/fs;
    vib=0.5*sin(2*pi*70*dt)+0.35*sin(2*pi*110*dt)+0.2*sin(2*pi*160*dt);
    rough=min(max(1.0+0.5*randn(1,len),0.2),1.8);
    env=ones(1,len); fl=min(floor(0.1*fs),floor(len/2));
    if fl>0, env(1:fl)=linspace(0,1,fl); env(end-fl+1:end)=linspace(1,0,fl); end
    signal(si:ei)=signal(si:ei)+vib.*rough.*env;
end
signal=signal+0.06*sin(2*pi*40*t)+0.06*randn(size(t));
end

function [signal,drop_times]=generate_object_drop_noise(t,fs,duration)
%#ok<INUSD>
signal=zeros(size(t)); drop_times=[1.2,3.7,6.1];
for k=1:length(drop_times)
    index=floor(drop_times(k)*fs)+1; strength=2.0+(3.2-2.0)*rand();
    burst_len=floor(0.6*fs);
    if index+burst_len-1>length(signal), burst_len=length(signal)-index+1; end
    if burst_len<=0, continue; end
    bt=(0:burst_len-1)/fs; env=exp(-7*bt);
    burst=strength*env.*(sin(2*pi*18*bt)+0.9*sin(2*pi*40*bt)+0.5*sin(2*pi*75*bt));
    sharp=zeros(1,burst_len); sl=min(35,burst_len); sharp(1:sl)=strength*2.8*exp(-linspace(0,6,sl));
    signal(index:index+burst_len-1)=signal(index:index+burst_len-1)+burst+sharp;
end
signal=signal+0.04*randn(size(t));
end

function [noise_type,features]=classify_noise(signal,fs)
sr=rms_value(signal); sp=peak_abs(signal); peak_to_rms=sp/(sr+1e-9);
N=length(signal); spectrum=abs(fft(signal)); halfN=floor(N/2)+1; spectrum=spectrum(1:halfN);
xf=(0:halfN-1)*fs/N; mask=(xf>=5)&(xf<=200); ls=spectrum(mask); lx=xf(mask);
if isempty(ls), dominant_ratio=0; dominant_freq=0;
else, [dp,idx]=max(ls); dominant_ratio=dp/(sum(ls)+1e-9); dominant_freq=lx(idx); end
ws=floor(0.2*fs); wr=[];
for st=1:ws:(length(signal)-ws), wr(end+1)=rms_value(signal(st:st+ws-1)); end %#ok<AGROW>
if ~isempty(wr), active_ratio=mean(wr>0.4*max(wr)); else, active_ratio=0; end
if dominant_ratio>0.20 && active_ratio>0.70
    noise_type="repetitive_vibration";
elseif active_ratio>=0.30 && peak_to_rms<7.0 && dominant_freq>=60 && dominant_freq<=180
    noise_type="continuous_friction";
else
    noise_type="impact_noise";
end
features.peak_to_rms=peak_to_rms; features.dominant_ratio=dominant_ratio; features.dominant_freq=dominant_freq; features.active_ratio=active_ratio;
end

function result=fixed_gain_delay_control(input_signal,filtered_signal,train_end,test_start,secondary_actual)
best_train_db=-999;
for delay_samples=0:80
    delayed=delay_signal(filtered_signal,delay_samples);
    for gain=0.1:0.1:1.0
        control_raw=-gain*delayed; control_actual=apply_secondary_path(control_raw,secondary_actual);
        output_signal=input_signal+control_actual;
        train_db=db_reduction(input_signal(1:train_end),output_signal(1:train_end));
        if train_db>best_train_db
            best_train_db=train_db; best_gain=gain; best_delay=delay_samples;
            best_control_raw=control_raw; best_control_actual=control_actual; best_output=output_signal;
        end
    end
end
result.method="Fixed Gain/Delay"; result.gain=best_gain; result.delay=best_delay; result.output=best_output;
result.control_raw=best_control_raw; result.control_actual=best_control_actual;
result.test_after_rms=rms_value(best_output(test_start:end));
result.test_reduction_db=db_reduction(input_signal(test_start:end),best_output(test_start:end));
result.test_peak_db=peak_db_reduction(input_signal(test_start:end),best_output(test_start:end));
result.control_ratio=rms_value(best_control_raw(test_start:end))/(rms_value(input_signal(test_start:end))+1e-9);
result.train_db=best_train_db;
end

function [control_raw,control_actual,output_signal,w]=run_fxnlms_control(input_signal,reference,filter_order,mu,control_limit,secondary_actual,secondary_estimated)
epsilon=1e-6; w=zeros(1,filter_order); control_raw=zeros(size(input_signal)); control_actual=zeros(size(input_signal)); output_signal=zeros(size(input_signal));
filtered_reference=apply_secondary_path(reference,secondary_estimated);
for n=filter_order+1:length(input_signal)
    x_vec=reference(n:-1:n-filter_order+1); xf_vec=filtered_reference(n:-1:n-filter_order+1);
    y_raw=-dot(w,x_vec); y_raw=min(max(y_raw,-control_limit),control_limit); control_raw(n)=y_raw;
    max_k=min(length(secondary_actual),n); recent=control_raw(n:-1:n-max_k+1); coeff=secondary_actual(1:length(recent));
    y_actual=dot(coeff,recent); control_actual(n)=y_actual; e=input_signal(n)+y_actual; output_signal(n)=e;
    w=w+(mu*e*xf_vec)/(epsilon+dot(xf_vec,xf_vec));
end
output_signal(1:filter_order)=input_signal(1:filter_order);
end

function result=fxnlms_adaptive_control(input_signal,filtered_signal,train_end,test_start,secondary_actual,secondary_estimated)
best_train_db=-999;
for filter_order=[16,32,64]
    for mu=[0.001,0.003,0.005,0.01,0.02,0.05]
        [cr,ca,out,w]=run_fxnlms_control(input_signal,filtered_signal,filter_order,mu,3.0,secondary_actual,secondary_estimated);
        train_db=db_reduction(input_signal(1:train_end),out(1:train_end));
        if train_db>best_train_db
            best_train_db=train_db; best_order=filter_order; best_mu=mu; best_cr=cr; best_ca=ca; best_out=out; best_w=w;
        end
    end
end
result.method="FxNLMS Adaptive"; result.filter_order=best_order; result.mu=best_mu; result.output=best_out;
result.control_raw=best_cr; result.control_actual=best_ca;
result.test_after_rms=rms_value(best_out(test_start:end));
result.test_reduction_db=db_reduction(input_signal(test_start:end),best_out(test_start:end));
result.test_peak_db=peak_db_reduction(input_signal(test_start:end),best_out(test_start:end));
result.control_ratio=rms_value(best_cr(test_start:end))/(rms_value(input_signal(test_start:end))+1e-9);
result.train_db=best_train_db; result.w=best_w;
end

function result=ringdown_control(input_signal,cutoff,fs,train_end,test_start,secondary_actual)
low=lowpass_filter(input_signal,cutoff,fs,4); threshold=2.5*rms_value(input_signal); min_distance=floor(0.25*fs);
impact_indices=[]; last_index=-min_distance;
for i=2:length(input_signal)-1
    if abs(input_signal(i))>threshold && i-last_index>=min_distance
        impact_indices(end+1)=i; last_index=i; %#ok<AGROW>
    end
end
best_train_db=-999;
for gain=[0.2,0.3,0.4,0.5,0.6]
    for delay=0:60
        control_raw=zeros(size(input_signal));
        for k=1:length(impact_indices)
            idx=impact_indices(k); si=idx+floor(0.04*fs); ei=idx+floor(0.50*fs);
            if si>length(input_signal), continue; end
            ei=min(ei,length(input_signal)); seg=low(si:ei); dseg=delay_signal(seg,delay); env=exp(-linspace(0,3,length(dseg)));
            control_raw(si:ei)=control_raw(si:ei)+(-gain*dseg.*env);
        end
        control_actual=apply_secondary_path(control_raw,secondary_actual); output_signal=input_signal+control_actual;
        train_db=db_reduction(input_signal(1:train_end),output_signal(1:train_end));
        if train_db>best_train_db
            best_train_db=train_db; best_gain=gain; best_delay=delay; best_cr=control_raw; best_ca=control_actual; best_out=output_signal;
        end
    end
end
rb=[]; ra=[];
for k=1:length(impact_indices)
    idx=impact_indices(k); si=idx+floor(0.04*fs); ei=idx+floor(0.50*fs);
    if si>=test_start && ei<=length(input_signal), rb=[rb,input_signal(si:ei)]; ra=[ra,best_out(si:ei)]; end %#ok<AGROW>
end
if ~isempty(rb), ringdown_db=db_reduction(rb,ra); else, ringdown_db=db_reduction(input_signal(test_start:end),best_out(test_start:end)); end
result.method="Ringdown Impact Control"; result.gain=best_gain; result.delay=best_delay; result.output=best_out;
result.control_raw=best_cr; result.control_actual=best_ca;
result.test_after_rms=rms_value(best_out(test_start:end));
result.test_reduction_db=db_reduction(input_signal(test_start:end),best_out(test_start:end));
result.test_peak_db=peak_db_reduction(input_signal(test_start:end),best_out(test_start:end));
result.ringdown_db=ringdown_db; result.impact_count=length(impact_indices);
result.control_ratio=rms_value(best_cr(test_start:end))/(rms_value(input_signal(test_start:end))+1e-9);
result.train_db=best_train_db;
end

function result=hybrid_control(input_signal,cutoff,fs,train_end,test_start,secondary_actual,secondary_estimated)
filtered=lowpass_filter(input_signal,cutoff,fs,4); [noise_type,features]=classify_noise(input_signal,fs);
fixed=fixed_gain_delay_control(input_signal,filtered,train_end,test_start,secondary_actual);
fx=fxnlms_adaptive_control(input_signal,filtered,train_end,test_start,secondary_actual,secondary_estimated);
ring=ringdown_control(input_signal,cutoff,fs,train_end,test_start,secondary_actual);
if noise_type=="repetitive_vibration" || noise_type=="continuous_friction"
    selected=fx; selected_mode="FxNLMS Adaptive";
else
    if fixed.test_reduction_db>=ring.test_reduction_db, selected=fixed; selected_mode="Fixed Gain/Delay"; else, selected=ring; selected_mode="Ringdown Impact Control"; end
end
vals=[fixed.test_reduction_db,fx.test_reduction_db,ring.test_reduction_db]; [~,idx]=max(vals);
if idx==1, best_name="Fixed Gain/Delay"; best_result=fixed; elseif idx==2, best_name="FxNLMS Adaptive"; best_result=fx; else, best_name="Ringdown Impact Control"; best_result=ring; end
result.noise_type=noise_type; result.features=features; result.selected_mode=selected_mode; result.selected_result=selected;
result.best_name=best_name; result.best_result=best_result; result.fixed_result=fixed; result.fxnlms_result=fx; result.ringdown_result=ring;
end

function r=run_experiment(name,name_kr,input_signal,event_info,cutoff,show_graph,fs,t,train_end,test_start,secondary_actual,secondary_estimated)
result=hybrid_control(input_signal,cutoff,fs,train_end,test_start,secondary_actual,secondary_estimated);
noise_type=result.noise_type; features=result.features; fixed=result.fixed_result; fx=result.fxnlms_result; ring=result.ringdown_result;
selected=result.selected_result; selected_mode=result.selected_mode; best_name=result.best_name; best=result.best_result;
type_kr=type_korean(noise_type); selected_kr=method_korean(selected_mode); test_before=input_signal(test_start:end);
fprintf("\n==========================================================================================\n");
fprintf("[실험] %s (%s)\n",name,name_kr);
fprintf("==========================================================================================\n");
fprintf("저역통과필터 cutoff: %d Hz\n",cutoff); fprintf("이벤트 정보: "); disp(event_info);
fprintf("평가 구간 제어 전 RMS: %.15f\n",rms_value(test_before));
fprintf("\n[소음 유형 분류]\n분류 결과: %s (%s)\n",noise_type,type_kr);
fprintf("peak/RMS: %.3f\n",features.peak_to_rms); fprintf("dominant frequency: %.3f Hz\n",features.dominant_freq);
fprintf("dominant ratio: %.4f\n",features.dominant_ratio); fprintf("active ratio: %.4f\n",features.active_ratio);
fprintf("\n[고정 gain/delay 방식]\nRMS 감소량 dB: %.15f\nPeak 감소량 dB: %.15f\ngain: %.1f\ndelay: %d\n제어비: %.15f\n",fixed.test_reduction_db,fixed.test_peak_db,fixed.gain,fixed.delay,fixed.control_ratio);
fprintf("\n[FxNLMS 적응형 방식]\nRMS 감소량 dB: %.15f\nPeak 감소량 dB: %.15f\nfilter_order: %d\nmu: %.3f\n제어비: %.15f\n",fx.test_reduction_db,fx.test_peak_db,fx.filter_order,fx.mu,fx.control_ratio);
fprintf("\n[충격 잔향 저감 방식]\nRMS 감소량 dB: %.15f\nPeak 감소량 dB: %.15f\n잔향 구간 감소량 dB: %.15f\n감지된 충격 수: %d\ngain: %.1f\ndelay: %d\n제어비: %.15f\n",ring.test_reduction_db,ring.test_peak_db,ring.ringdown_db,ring.impact_count,ring.gain,ring.delay,ring.control_ratio);
fprintf("\n[하이브리드 선택 결과]\n선택된 제어 방식: %s (%s)\n선택 방식 RMS 감소량 dB: %.15f\n선택 방식 Peak 감소량 dB: %.15f\n",selected_mode,selected_kr,selected.test_reduction_db,selected.test_peak_db);
fprintf("\n[참고: 실제 최고 성능 방식]\n최고 성능 방식: %s (%s)\n최고 성능 RMS 감소량 dB: %.15f\n",best_name,method_korean(best_name),best.test_reduction_db);
if show_graph
    figure('Name',char(name)); subplot(5,1,1); plot(t,input_signal); xline(t(train_end),'--'); title(name+" ("+name_kr+") - Input Signal");
    subplot(5,1,2); plot(t,fixed.output); xline(t(train_end),'--'); title(sprintf("Fixed Gain/Delay (%.2f dB)",fixed.test_reduction_db));
    subplot(5,1,3); plot(t,fx.output); xline(t(train_end),'--'); title(sprintf("FxNLMS Adaptive (%.2f dB)",fx.test_reduction_db));
    subplot(5,1,4); plot(t,ring.output); xline(t(train_end),'--'); title(sprintf("Ringdown (%.2f dB, Ringdown %.2f dB)",ring.test_reduction_db,ring.ringdown_db));
    subplot(5,1,5); plot(t,selected.control_actual); xline(t(train_end),'--'); title("Selected Actual Control Signal: "+selected_mode);
    figure('Name',char(name+" FFT")); N=length(test_before); xf_axis=(0:floor(N/2))*fs/N;
    signals={test_before,fixed.output(test_start:end),fx.output(test_start:end),ring.output(test_start:end),selected.output(test_start:end)};
    labels=["Before","Fixed","FxNLMS","Ringdown","Selected"]; hold on;
    for q=1:length(signals), sp=abs(fft(signals{q})); plot(xf_axis,sp(1:length(xf_axis)),'DisplayName',labels(q)); end
    xlim([0 200]); title(name+" FFT Comparison"); xlabel("Frequency [Hz]"); ylabel("Magnitude"); legend; grid on;
end
r.name=string(name); r.name_kr=string(name_kr); r.noise_type=noise_type; r.noise_type_kr=type_kr; r.test_before_rms=rms_value(test_before);
r.fixed_db=fixed.test_reduction_db; r.fxnlms_db=fx.test_reduction_db; r.ringdown_db=ring.test_reduction_db; r.ringdown_only_db=ring.ringdown_db;
r.selected_mode=selected_mode; r.selected_mode_kr=selected_kr; r.selected_db=selected.test_reduction_db; r.selected_peak_db=selected.test_peak_db;
r.best_name=best_name; r.best_name_kr=method_korean(best_name); r.best_db=best.test_reduction_db;
end

function kr=type_korean(noise_type)
switch char(noise_type)
    case 'impact_noise', kr="충격성 소음";
    case 'repetitive_vibration', kr="반복 진동";
    case 'continuous_friction', kr="연속 마찰음";
    otherwise, kr=string(noise_type);
end
end

function kr=method_korean(method)
switch char(method)
    case 'Fixed Gain/Delay', kr="고정 이득/지연 제어";
    case 'FxNLMS Adaptive', kr="FxNLMS 적응형 제어";
    case 'Ringdown Impact Control', kr="충격 잔향 저감 제어";
    otherwise, kr=string(method);
end
end
