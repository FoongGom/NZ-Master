"""
classdef SensorInput < handle
% SensorInput  층간소음 ANC 센서 & 입력 처리 클래스 (MATLAB 단일 파일)
%
% 담당자: 이형규 / 역할: 센서 & 입력
%
% =====================================================
% 핵심 목표: 전체 파이프라인 레이턴시 0.3ms 이하
% =====================================================
%
% 층간소음 ANC 흐름:
%   마이크 입력
%     → [SensorInput] 전처리 (0.3ms 이내)
%     → 메인 제어 코드 (hybrid_control)
%     → 스피커 출력 (역위상 신호로 소음 상쇄)
%
% ─────────────────────────────────────────────────────
% 【단독 테스트 실행】
%   SensorInput.run_test()
%
% 【기본 사용】
%   sensor = SensorInput(1000, 8.0);
%   sensor.setup_i2s();
%   sig = sensor.read_i2s_mic('child_running');
%
% 【하이브리드 코드 연동】
%   [stable_sig, cutoff] = SensorInput.get_stable_signal('child_running');
%
% 【실시간 ANC 루프】
%   out = SensorInput.anc_loop('child_running', sensor);
% ─────────────────────────────────────────────────────

    % =========================================================
    % 프로퍼티
    % =========================================================
    properties
        fs                (1,1) double = 1000
        duration          (1,1) double = 8.0
        t                 (1,:) double
        random_seed       (1,1) double = 10

        i2s_config        struct
        vibration_config  struct

        collected         struct
        latency_log       (1,:) double = []

        dc_filter_x_prev  (1,1) double = 0.0
        dc_filter_y_prev  (1,1) double = 0.0
    end

    % =========================================================
    % 생성자
    % =========================================================
    methods
        function obj = SensorInput(fs, duration, random_seed)
            if nargin < 1, fs          = 1000; end
            if nargin < 2, duration    = 8.0;  end
            if nargin < 3, random_seed = 10;   end

            obj.fs          = fs;
            obj.duration    = duration;
            obj.random_seed = random_seed;
            obj.t           = 0 : 1/fs : duration - 1/fs;

            obj.i2s_config = struct( ...
                'fs',                  fs, ...
                'bit_depth',           24, ...
                'sensitivity_scale',   1.0, ...
                'noise_floor',         0.001, ...
                'dc_offset_threshold', 0.01, ...
                'clip_limit',          0.95, ...
                'target_latency_ms',   0.3, ...
                'buffer_samples',      SensorInput.max_buffer_samples(fs), ...
                'dc_filter_alpha',     0.995 ...
            );

            obj.vibration_config = struct( ...
                'freq_range',      [20, 200], ...
                'gain',            1.2, ...
                'median_kernel',   5, ...
                'highpass_cutoff', 5, ...
                'lowpass_cutoff',  200 ...
            );

            obj.collected = struct();
            rng(obj.random_seed);
        end
    end

    % =========================================================
    % 1. I2S 마이크 세팅
    % =========================================================
    methods
        function cfg = setup_i2s(obj, fs, sensitivity_scale)
            % setup_i2s  I2S 마이크 설정 출력
            %   cfg = sensor.setup_i2s()
            %   cfg = sensor.setup_i2s(fs, sensitivity_scale)

            if nargin >= 2 && ~isempty(fs)
                obj.fs                        = fs;
                obj.i2s_config.fs             = fs;
                obj.i2s_config.buffer_samples = SensorInput.max_buffer_samples(fs);
                obj.t                         = 0 : 1/fs : obj.duration - 1/fs;
            end
            if nargin >= 3 && ~isempty(sensitivity_scale)
                obj.i2s_config.sensitivity_scale = sensitivity_scale;
            end

            buf            = obj.i2s_config.buffer_samples;
            actual_latency = buf / obj.fs * 1000;

            fprintf('[I2S 마이크 세팅]\n');
            flds = fieldnames(obj.i2s_config);
            for i = 1:numel(flds)
                fprintf('  %s: %s\n', flds{i}, num2str(obj.i2s_config.(flds{i})));
            end
            fprintf('  -> 버퍼 %d샘플 = 실제 레이턴시 %.4fms (목표: 0.3ms)\n', ...
                buf, actual_latency);

            cfg = obj.i2s_config;
        end

        function y = read_sample_i2s(obj, raw_sample)
            % read_sample_i2s  1샘플 전처리 - 0.3ms 목표 핵심 함수

            s     = raw_sample * obj.i2s_config.sensitivity_scale;
            alpha = obj.i2s_config.dc_filter_alpha;
            y     = s - obj.dc_filter_x_prev + alpha * obj.dc_filter_y_prev;
            obj.dc_filter_x_prev = s;
            obj.dc_filter_y_prev = y;

            cl = obj.i2s_config.clip_limit;
            y  = max(-cl, min(cl, y));
        end

        function processed = read_i2s_mic(obj, noise_type)
            % read_i2s_mic  전체 신호를 샘플 단위로 처리

            if nargin < 2, noise_type = 'child_running'; end

            raw = obj.simulate_noise(noise_type);
            obj.reset_dc_filter();

            processed = zeros(size(raw));
            for n = 1:numel(raw)
                processed(n) = obj.read_sample_i2s(raw(n));
            end

            fprintf('[I2S 마이크 읽기] noise_type=%s, RMS=%.5f, peak=%.5f, samples=%d\n', ...
                noise_type, si_rms(processed), max(abs(processed)), numel(processed));
        end
    end

    % =========================================================
    % 2. 진동 센서 튜닝
    % =========================================================
    methods
        function cfg = tune_vibration_sensor(obj, gain, freq_range, median_kernel)
            % tune_vibration_sensor  진동 센서 파라미터 설정

            if nargin >= 2 && ~isempty(gain)
                obj.vibration_config.gain = gain;
            end
            if nargin >= 3 && ~isempty(freq_range)
                if freq_range(1) >= freq_range(2)
                    error('freq_range(1)은 freq_range(2)보다 작아야 합니다.');
                end
                obj.vibration_config.freq_range      = freq_range;
                obj.vibration_config.highpass_cutoff = freq_range(1);
                obj.vibration_config.lowpass_cutoff  = freq_range(2);
            end
            if nargin >= 4 && ~isempty(median_kernel)
                if mod(median_kernel, 2) == 0
                    error('median_kernel은 홀수여야 합니다.');
                end
                obj.vibration_config.median_kernel = median_kernel;
            end

            fprintf('[진동 센서 튜닝]\n');
            flds = fieldnames(obj.vibration_config);
            for i = 1:numel(flds)
                fprintf('  %s: %s\n', flds{i}, num2str(obj.vibration_config.(flds{i})));
            end

            cfg = obj.vibration_config;
        end

        function out = read_vibration_sensor(obj, noise_type)
            % read_vibration_sensor  진동 센서 시뮬레이션

            if nargin < 2, noise_type = 'child_running'; end

            gained = obj.simulate_noise(noise_type) * obj.vibration_config.gain;

            hp = obj.vibration_config.highpass_cutoff;
            if hp > 0
                gained = si_butter(gained, hp, obj.fs, 'high');
            end

            lp = obj.vibration_config.lowpass_cutoff;
            if lp < obj.fs / 2
                gained = si_butter(gained, lp, obj.fs, 'low');
            end

            k = obj.vibration_config.median_kernel;
            if k > 1
                gained = medfilt1(gained, k);
            end

            fprintf('[진동 센서 읽기] noise_type=%s, RMS=%.5f, peak=%.5f\n', ...
                noise_type, si_rms(gained), max(abs(gained)));

            out = gained;
        end
    end

    % =========================================================
    % 3. 소음 데이터 수집
    % =========================================================
    methods
        function signal = collect(obj, noise_type, source, label)
            % collect  단일 소음 유형 수집
            %   signal = sensor.collect('child_running')
            %   signal = sensor.collect('child_running', 'vibration', 'label')

            if nargin < 3, source = 'i2s';      end
            if nargin < 4, label  = noise_type; end

            if strcmp(source, 'vibration')
                signal = obj.read_vibration_sensor(noise_type);
            else
                signal = obj.read_i2s_mic(noise_type);
            end

            key = matlab.lang.makeValidName(label);
            obj.collected.(key) = signal;

            fprintf('[수집 완료] key=''%s'', source=%s, samples=%d\n', ...
                key, source, numel(signal));
        end

        function result = collect_all(obj, source)
            % collect_all  5가지 소음 유형 전체 수집

            if nargin < 2, source = 'i2s'; end

            types = {'child_running','adult_footstep','washing_machine', ...
                     'chair_dragging','object_drop'};

            fprintf('\n[전체 소음 데이터 수집 시작]\n');
            for i = 1:numel(types)
                obj.collect(types{i}, source);
            end
            fprintf('[전체 수집 완료] 총 %d개 유형\n\n', numel(types));

            result = obj.collected;
        end

        function signal = get_collected(obj, label)
            % get_collected  수집된 신호 반환 (label 생략 시 전체 struct)

            if nargin < 2 || isempty(label)
                signal = obj.collected;
                return;
            end
            key = matlab.lang.makeValidName(label);
            if ~isfield(obj.collected, key)
                error('''%s'' 키가 수집 데이터에 없습니다.', label);
            end
            signal = obj.collected.(key);
        end
    end

    % =========================================================
    % 4. 신호 안정화
    % =========================================================
    methods
        function y = stabilize_sample(obj, sample)
            cl = obj.i2s_config.clip_limit;
            y  = max(-cl, min(cl, sample));
        end

        function s = stabilize(obj, signal, remove_dc, remove_outliers, do_clip, normalize)
            % stabilize  오프라인 신호 안정화
            %   s = sensor.stabilize(signal)               % 모든 옵션 true
            %   s = sensor.stabilize(signal, true, true, true, true)

            if nargin < 3, remove_dc       = true; end
            if nargin < 4, remove_outliers = true; end
            if nargin < 5, do_clip         = true; end
            if nargin < 6, normalize       = true; end

            s = signal(:)';

            if remove_dc
                dc = mean(s);
                if abs(dc) > obj.i2s_config.dc_offset_threshold
                    s = s - dc;
                    fprintf('[안정화] DC 오프셋 제거: %.5f\n', dc);
                end
            end

            if remove_outliers
                mu_s  = mean(s);
                sig_s = std(s);
                mask  = (s > mu_s + 3*sig_s) | (s < mu_s - 3*sig_s);
                if any(mask)
                    sf      = medfilt1(s, obj.vibration_config.median_kernel);
                    s(mask) = sf(mask);
                    fprintf('[안정화] 이상치 제거: %d개 샘플\n', sum(mask));
                end
            end

            if do_clip
                cl = obj.i2s_config.clip_limit;
                nc = sum(abs(s) > cl);
                if nc > 0
                    s = max(-cl, min(cl, s));
                    fprintf('[안정화] 클리핑 처리: %d개 샘플\n', nc);
                end
            end

            if normalize
                pk = max(abs(s));
                if pk > 0
                    s = s / pk;
                    fprintf('[안정화] 정규화 완료: peak=%.5f -> 1.0\n', pk);
                end
            end

            fprintf('[안정화 결과] RMS=%.5f, peak=%.5f\n', si_rms(s), max(abs(s)));
        end

        function result = stabilize_all(obj, signals, varargin)
            % stabilize_all  수집된 모든 신호 일괄 안정화

            if nargin < 2 || isempty(signals)
                signals = obj.collected;
            end
            if isempty(fieldnames(signals))
                error('안정화할 신호가 없습니다.');
            end

            result = struct();
            keys   = fieldnames(signals);
            for i = 1:numel(keys)
                k = keys{i};
                fprintf('\n[안정화 시작] ''%s''\n', k);
                result.(k) = obj.stabilize(signals.(k), varargin{:});
            end
        end
    end

    % =========================================================
    % 5. 레이턴시 측정
    % =========================================================
    methods
        function [processed, elapsed_ms] = measure_latency_sample(obj, raw_sample)
            t0         = tic;
            processed  = obj.read_sample_i2s(raw_sample);
            elapsed_ms = toc(t0) * 1000;
            obj.latency_log(end+1) = elapsed_ms;
        end

        function report = latency_report(obj)
            % latency_report  레이턴시 통계 출력

            if isempty(obj.latency_log)
                fprintf('[레이턴시 리포트] 측정 데이터 없음.\n');
                report = struct();
                return;
            end

            log    = obj.latency_log;
            within = mean(log <= 0.3) * 100;

            report = struct( ...
                'samples_measured',  numel(log), ...
                'avg_ms',            round(mean(log), 4), ...
                'max_ms',            round(max(log),  4), ...
                'min_ms',            round(min(log),  4), ...
                'target_ms',         0.3, ...
                'within_target_pct', round(within, 1) ...
            );

            fprintf('\n[레이턴시 리포트]\n');
            flds = fieldnames(report);
            for i = 1:numel(flds)
                fprintf('  %s: %s\n', flds{i}, num2str(report.(flds{i})));
            end

            if within < 95
                fprintf('  ⚠ 경고: %.1f%%의 샘플이 목표를 초과함\n', 100 - within);
            else
                fprintf('  ✓ 목표 달성: %.1f%%의 샘플이 0.3ms 이내\n', within);
            end
        end
    end

    % =========================================================
    % 내부 헬퍼 (simulate_noise는 anc_loop에서도 호출)
    % =========================================================
    methods (Access = public)
        function reset_dc_filter(obj)
            obj.dc_filter_x_prev = 0.0;
            obj.dc_filter_y_prev = 0.0;
        end

        function signal = simulate_noise(obj, noise_type)
            switch noise_type
                case 'child_running',   signal = obj.gen_child_running();
                case 'adult_footstep',  signal = obj.gen_adult_footstep();
                case 'washing_machine', signal = obj.gen_washing_machine();
                case 'chair_dragging',  signal = obj.gen_chair_dragging();
                case 'object_drop',     signal = obj.gen_object_drop();
                otherwise
                    error(['알 수 없는 noise_type: ''%s''. ' ...
                        'child_running | adult_footstep | washing_machine | ' ...
                        'chair_dragging | object_drop 중 선택하세요.'], noise_type);
            end
        end
    end

    % =========================================================
    % 신호 생성 (private)
    % =========================================================
    methods (Access = private)
        function signal = gen_child_running(obj)
            t = obj.t; fs = obj.fs; dur = obj.duration;
            signal = zeros(1, numel(t));
            ct = 0.4;
            while ct < dur - 0.5
                ct  = ct + 0.25 + rand*(0.45-0.25);
                idx = round(ct * fs);
                if idx < 1 || idx > numel(signal), continue; end
                str = 0.8 + rand*(1.5-0.8);
                bl  = min(round(0.25*fs), numel(signal)-idx);
                if bl <= 0, continue; end
                bt  = (0:bl-1)/fs;
                env = exp(-18*bt);
                burst = str*env.*(sin(2*pi*30*bt)+0.8*sin(2*pi*55*bt)+0.4*sin(2*pi*90*bt));
                sl = min(20,bl); sh = zeros(1,bl);
                sh(1:sl) = str*1.8*exp(-linspace(0,4,sl));
                signal(idx:idx+bl-1) = signal(idx:idx+bl-1) + burst + sh;
            end
            signal = signal + 0.12*sin(2*pi*25*t) + 0.08*sin(2*pi*45*t) ...
                     + 0.05*randn(1,numel(t));
        end

        function signal = gen_adult_footstep(obj)
            t = obj.t; fs = obj.fs; dur = obj.duration;
            signal = zeros(1, numel(t));
            ct = 0.6;
            while ct < dur - 0.5
                ct  = ct + 0.55 + rand*(0.85-0.55);
                idx = round(ct * fs);
                if idx < 1 || idx > numel(signal), continue; end
                str = 1.3 + rand*(2.2-1.3);
                bl  = min(round(0.35*fs), numel(signal)-idx);
                if bl <= 0, continue; end
                bt  = (0:bl-1)/fs;
                env = exp(-10*bt);
                burst = str*env.*(sin(2*pi*20*bt)+0.9*sin(2*pi*35*bt)+0.5*sin(2*pi*60*bt));
                sl = min(25,bl); sh = zeros(1,bl);
                sh(1:sl) = str*2.2*exp(-linspace(0,5,sl));
                signal(idx:idx+bl-1) = signal(idx:idx+bl-1) + burst + sh;
            end
            signal = signal + 0.08*sin(2*pi*30*t) + 0.04*randn(1,numel(t));
        end

        function signal = gen_washing_machine(obj)
            t = obj.t;
            signal = (0.8*sin(2*pi*45*t) + 0.5*sin(2*pi*90*t) + 0.25*sin(2*pi*135*t)) ...
                     .* (1.0 + 0.2*sin(2*pi*0.5*t)) + 0.04*randn(1,numel(t));
        end

        function signal = gen_chair_dragging(obj)
            t = obj.t; fs = obj.fs;
            signal = zeros(1, numel(t));
            segs = [1.0,2.0; 3.0,3.8; 5.2,6.4];
            for k = 1:size(segs,1)
                si = max(round(segs(k,1)*fs)+1, 1);
                ei = min(round(segs(k,2)*fs), numel(signal));
                ln = ei - si + 1;
                if ln <= 0, continue; end
                dt  = (0:ln-1)/fs;
                vib = 0.5*sin(2*pi*70*dt)+0.35*sin(2*pi*110*dt)+0.2*sin(2*pi*160*dt);
                rgh = min(max(1.0+0.5*randn(1,ln),0.2),1.8);
                fl  = min(round(0.1*fs), floor(ln/2));
                env = ones(1,ln);
                if fl > 0
                    env(1:fl)         = linspace(0,1,fl);
                    env(end-fl+1:end) = linspace(1,0,fl);
                end
                signal(si:ei) = signal(si:ei) + vib.*rgh.*env;
            end
            signal = signal + 0.06*sin(2*pi*40*t) + 0.06*randn(1,numel(t));
        end

        function signal = gen_object_drop(obj)
            t = obj.t; fs = obj.fs;
            signal = zeros(1, numel(t));
            for drop_t = [1.2, 3.7, 6.1]
                idx = round(drop_t*fs)+1;
                if idx < 1 || idx > numel(signal), continue; end
                str = 2.0 + rand*(3.2-2.0);
                bl  = min(round(0.6*fs), numel(signal)-idx+1);
                if bl <= 0, continue; end
                bt  = (0:bl-1)/fs;
                env = exp(-7*bt);
                burst = str*env.*(sin(2*pi*18*bt)+0.9*sin(2*pi*40*bt)+0.5*sin(2*pi*75*bt));
                sl = min(35,bl); sh = zeros(1,bl);
                sh(1:sl) = str*2.8*exp(-linspace(0,6,sl));
                signal(idx:idx+bl-1) = signal(idx:idx+bl-1) + burst + sh;
            end
            signal = signal + 0.04*randn(1,numel(t));
        end
    end

    % =========================================================
    % Static: 상수 / ANC 루프 / 하이브리드 연동 / 테스트
    % =========================================================
    methods (Static)

        % ── 상수 ──────────────────────────────────────────────
        function v = TARGET_LATENCY_MS(),  v = 0.3;        end
        function v = TARGET_LATENCY_SEC(), v = 0.3/1000.0; end

        function m = NOISE_META()
            m = struct( ...
                'child_running',   struct('name','Child Running Noise',        'cutoff',150), ...
                'adult_footstep',  struct('name','Adult Heavy Footstep Noise', 'cutoff',120), ...
                'washing_machine', struct('name','Washing Machine Vibration',  'cutoff',180), ...
                'chair_dragging',  struct('name','Chair Dragging Noise',       'cutoff',200), ...
                'object_drop',     struct('name','Object Drop Impact Noise',   'cutoff',120)  ...
            );
        end

        function n = max_buffer_samples(fs)
            n = max(floor(fs * SensorInput.TARGET_LATENCY_SEC()), 1);
        end

        % ── 실시간 ANC 루프 ───────────────────────────────────
        function output_signal = anc_loop(noise_type, sensor, anc_callback)
            % anc_loop  실시간 ANC 시뮬레이션 루프
            %
            % 사용 예:
            %   sensor = SensorInput(1000, 8.0);
            %   out = SensorInput.anc_loop('child_running', sensor);
            %   out = SensorInput.anc_loop('child_running', sensor, @(x)-x*0.9);

            if nargin < 3 || isempty(anc_callback)
                anc_callback = @(x) -x;
            end

            raw = sensor.simulate_noise(noise_type);
            sensor.reset_dc_filter();

            N             = numel(raw);
            output_signal = zeros(1, N);
            latencies     = zeros(1, N);

            for n = 1:N
                t0               = tic;
                ps               = sensor.read_sample_i2s(raw(n));
                output_signal(n) = anc_callback(ps);
                latencies(n)     = toc(t0) * 1000;
            end

            within = mean(latencies <= 0.3) * 100;
            fprintf('\n[실시간 ANC 루프 완료] noise_type=%s\n', noise_type);
            fprintf('  평균 레이턴시: %.4fms  최대: %.4fms\n', mean(latencies), max(latencies));
            fprintf('  목표(0.3ms) 달성률: %.1f%%\n', within);
            if within < 95
                fprintf('  ⚠ 경고: %.1f%%의 샘플이 목표를 초과함\n', 100-within);
            else
                fprintf('  ✓ 목표 달성\n');
            end
        end

        % ── 하이브리드 코드 연동 진입점 ───────────────────────
        function [stable_signal, cutoff] = get_stable_signal(noise_type, fs, duration, source)
            % get_stable_signal  하이브리드 코드 연동 진입점
            %   수집 -> 안정화(DC제거 + 이상치제거 + 정규화) 후 반환
            %
            % 사용 예:
            %   [sig, co] = SensorInput.get_stable_signal('child_running');
            %
            % 하이브리드 코드 섹션 11 교체 예시:
            %   [s, co] = SensorInput.get_stable_signal('child_running');
            %   experiments{end+1} = run_experiment('Child Running Noise', s, ...
            %       'from sensor_input', co, SHOW_GRAPHS);

            if nargin < 2 || isempty(fs),       fs       = 1000;  end
            if nargin < 3 || isempty(duration), duration = 8.0;   end
            if nargin < 4 || isempty(source),   source   = 'i2s'; end

            sensor        = SensorInput(fs, duration);
            raw           = sensor.collect(noise_type, source);
            stable_signal = sensor.stabilize(raw, true, true, true, true);

            meta = SensorInput.NOISE_META();
            if isfield(meta, noise_type)
                cutoff = meta.(noise_type).cutoff;
            else
                cutoff = 150;
            end
        end

        % ── 단독 테스트 ───────────────────────────────────────
        function run_test()
            % run_test  단독 테스트 실행
            %   Python의 if __name__ == "__main__": 에 해당
            %
            % 실행 방법:
            %   SensorInput.run_test()

            % 상수를 지역 변수로 선언 (클래스 로드 이전 Static 호출 오류 방지)
            TARGET_MS = 0.3;

            fprintf('%s\n', repmat('=',1,60));
            fprintf('sensor_input (MATLAB) 단독 테스트\n');
            fprintf('목표 레이턴시: %.1fms\n', TARGET_MS);
            fprintf('%s\n\n', repmat('=',1,60));

            % 1. SensorInput 생성
            sensor = SensorInput(1000, 8.0);

            % 2. I2S 마이크 세팅
            fprintf('--- I2S 마이크 세팅 ---\n');
            sensor.setup_i2s();

            % 3. 진동 센서 튜닝
            fprintf('\n--- 진동 센서 튜닝 ---\n');
            sensor.tune_vibration_sensor(1.5, [20, 200], 5);

            % 4. 실시간 ANC 루프 테스트
            fprintf('\n--- 실시간 ANC 루프 테스트 ---\n');
            output = SensorInput.anc_loop('child_running', sensor);
            fprintf('  출력 신호 RMS: %.5f\n', si_rms(output));

            % 5. 1샘플 레이턴시 측정 (100회)
            fprintf('\n--- 1샘플 레이턴시 측정 (100회) ---\n');
            sensor2 = SensorInput(1000, 8.0);
            for k = 1:100
                sensor2.measure_latency_sample(randn());
            end
            sensor2.latency_report();

            % 6. 전체 소음 수집
            fprintf('\n--- 전체 소음 수집 ---\n');
            collected = sensor.collect_all('i2s');

            % 7. 수집 결과 요약
            fprintf('\n--- 수집 결과 요약 ---\n');
            keys = fieldnames(collected);
            for i = 1:numel(keys)
                k   = keys{i};
                sig = collected.(k);
                fprintf('  %s: RMS=%.5f, peak=%.5f, samples=%d\n', ...
                    k, si_rms(sig), max(abs(sig)), numel(sig));
            end

            % 8. 하이브리드 코드 연동 테스트
            fprintf('\n--- 하이브리드 코드 연동 테스트 ---\n');
            [sig_w, cutoff] = SensorInput.get_stable_signal('washing_machine');
            fprintf('  washing_machine -> RMS=%.5f, cutoff=%dHz\n', si_rms(sig_w), cutoff);
            fprintf('  신호 길이: %d (기대값 8000 일치: %d)\n', ...
                numel(sig_w), numel(sig_w)==8000);

            fprintf('\n[완료] 목표 레이턴시: %.1fms\n', TARGET_MS);

            % ── 하이브리드 코드 섹션 11 교체 예시 (주석) ──────
            %
            % experiments = {};
            %
            % [s,co] = SensorInput.get_stable_signal('child_running');
            % experiments{end+1} = run_experiment('Child Running Noise', ...
            %     s, 'from sensor_input', co, SHOW_GRAPHS);
            %
            % [s,co] = SensorInput.get_stable_signal('adult_footstep');
            % experiments{end+1} = run_experiment('Adult Heavy Footstep Noise', ...
            %     s, 'from sensor_input', co, SHOW_GRAPHS);
            %
            % [s,co] = SensorInput.get_stable_signal('washing_machine');
            % experiments{end+1} = run_experiment('Washing Machine Vibration', ...
            %     s, 'from sensor_input', co, SHOW_GRAPHS);
            %
            % [s,co] = SensorInput.get_stable_signal('chair_dragging');
            % experiments{end+1} = run_experiment('Chair Dragging Noise', ...
            %     s, 'from sensor_input', co, SHOW_GRAPHS);
            %
            % [s,co] = SensorInput.get_stable_signal('object_drop');
            % experiments{end+1} = run_experiment('Object Drop Impact Noise', ...
            %     s, 'from sensor_input', co, SHOW_GRAPHS);

        end

    end  % methods (Static)
end  % classdef SensorInput


% =========================================================
% 파일 로컬 헬퍼 함수
% classdef 외부 / 같은 .m 파일 안에서만 유효
% =========================================================

function y = si_butter(signal, cutoff, fs, btype)
    % si_butter  4차 Butterworth 필터 (Signal Processing Toolbox 필요)
    nyq   = fs / 2;
    wn    = min(max(cutoff/nyq, 1e-4), 0.9999);
    [b,a] = butter(4, wn, btype);
    y     = filter(b, a, signal);
end

function v = si_rms(signal)
    % si_rms  RMS 계산
    v = sqrt(mean(signal .^ 2));
end

"""

실행 결과:

>> SensorInput

ans = 

  SensorInput - 속성 있음:

                  fs: 1000
            duration: 8
                   t: [0 1.0000e-03 0.0020 0.0030 0.0040 0.0050 … ] (1×8000 double)
         random_seed: 10
          i2s_config: [1×1 struct]
    vibration_config: [1×1 struct]
           collected: [1×1 struct]
         latency_log: [1×0 double]
    dc_filter_x_prev: 0
    dc_filter_y_prev: 0

>> 
"""
