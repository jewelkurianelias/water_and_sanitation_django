from django.test import TestCase
from main.services import future_family_safety_service as ffs

from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import tempfile
import json
import joblib
import os
from pathlib import Path


class FutureFamilySafetyServiceTest(TestCase):
    """Tests for the main functions of future_family_safety_service"""

    def setUp(self):
        """Set up the test environment"""
        # Create a temporary directory structure to simulate artifacts
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        
        # Set environment variables
        self.original_env = {}
        self.original_env['FFS_MAX_HORIZON_DAYS'] = os.environ.get('FFS_MAX_HORIZON_DAYS', '')
        self.original_env['FFS_JOBLIB_MMAP'] = os.environ.get('FFS_JOBLIB_MMAP', '')
        
        os.environ['FFS_MAX_HORIZON_DAYS'] = '100'
        os.environ['FFS_JOBLIB_MMAP'] = ''
        
        # Create necessary directory structure
        artifacts_dir = self.base_dir / "artifacts"
        artifacts_dir.mkdir()
        clean_dir = artifacts_dir / "clean"
        clean_dir.mkdir()
        models_dir = artifacts_dir / "models"
        models_dir.mkdir()
        stats_dir = artifacts_dir / "stats"
        stats_dir.mkdir()
        
        # Create model directories for each parameter
        for param in ffs.PARAMS_CORE:
            (models_dir / param).mkdir(exist_ok=True)
        
        # Create configuration directory
        config_dir = self.base_dir / "config"
        config_dir.mkdir(exist_ok=True)
        
        # Patch base directory detection
        self.patch_base_dir = patch('main.services.future_family_safety_service._detect_base_dir')
        self.mock_base_dir = self.patch_base_dir.start()
        self.mock_base_dir.return_value = self.base_dir
        
        # Clear caches to ensure test isolation
        ffs.clear_caches()

    def tearDown(self):
        """Clean up the test environment"""
        self.temp_dir.cleanup()
        self.patch_base_dir.stop()
        
        # Restore environment variables
        for key, value in self.original_env.items():
            if value:
                os.environ[key] = value
            elif key in os.environ:
                del os.environ[key]
        
        ffs.clear_caches()

    def test_iso_utc(self):
        """Test ISO UTC datetime formatting"""
        dt_with_tz = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = ffs.iso_utc(dt_with_tz)
        self.assertEqual(result, "2023-01-01T12:00:00Z")
        
        dt_naive = datetime(2023, 1, 1, 12, 0, 0)
        result = ffs.iso_utc(dt_naive)
        self.assertEqual(result, "2023-01-01T12:00:00Z")
        
        dt_est = datetime(2023, 1, 1, 7, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
        result = ffs.iso_utc(dt_est)
        self.assertEqual(result, "2023-01-01T12:00:00Z")

    def test_file_safe(self):
        """Test filename sanitization"""
        self.assertEqual(ffs.file_safe("Test Site 123"), "Test_Site_123")
        self.assertEqual(ffs.file_safe("Site@With#Special$Chars"), "Site_With_Special_Chars")
        self.assertEqual(ffs.file_safe("Normal-name.txt"), "Normal-name.txt")

    def test_load_config_with_missing_file(self):
        """Test loading a missing configuration file"""
        config = ffs.load_config()
        self.assertIn('weights', config)
        self.assertIn('categories', config)
        self.assertIn('default_base_score', config)
        self.assertEqual(config['default_base_score'], 65.0)

    def test_load_config_with_existing_file(self):
        """Test loading an existing configuration file"""
        config_path = self.base_dir / "config" / "quality_rules.yaml"
        config_content = """
        weights:
          pH: 0.35
          EC_uS_cm: 0.3
          DO_mg_L: 0.2
          redox_mV: 0.1
        categories:
          good: 80.0
          fair: 50.0
          poor: 0.0
        default_base_score: 70.0
        """
        config_path.write_text(config_content)
        
        config = ffs.load_config()
        self.assertEqual(config['categories']['good'], 70.0)
        self.assertEqual(config['categories']['fair'], 40.0)
        self.assertEqual(config['categories']['poor'], 0.0)
        
    @patch('pandas.read_csv', return_value=pd.DataFrame())
    def test_get_clean_data_with_missing_files(self, mock_read_csv):
        """Test getting clean data when files are missing"""
        ffs.clear_caches()
        field_df, lab_df = ffs.get_clean_data()

        self.assertTrue(field_df.empty)
        self.assertTrue(lab_df.empty)

    @patch('pandas.read_csv')
    def test_get_clean_data_with_existing_files(self, mock_read_csv):
        """Test getting clean data when files exist"""
        test_data = pd.DataFrame({
            'site_id': ['site1', 'site2'],
            'datetime': ['2023-01-01', '2023-01-02'],
            'pH': [7.0, 7.5],
            'EC_uS_cm': [100, 200]
        })
        mock_read_csv.return_value = test_data
        
        field_df, lab_df = ffs.get_clean_data()
        self.assertFalse(field_df.empty)
        self.assertFalse(lab_df.empty)
        self.assertEqual(len(field_df), 2)
        self.assertEqual(len(lab_df), 2)

    def test_compute_stats_for_scoring(self):
        """Test computing statistics for scoring"""
        field_data = pd.DataFrame({
            'site_id': ['site1', 'site1', 'site2'],
            'datetime': ['2023-01-01', '2023-01-02', '2023-01-03'],
            'pH': [7.0, 7.5, 8.0],
            'EC_uS_cm': [100, 200, 300]
        })
        
        lab_data = pd.DataFrame({
            'site_id': ['site1', 'site2'],
            'datetime': ['2023-01-04', '2023-01-05'],
            'pH': [6.5, 7.2],
            'DO_mg_L': [8.0, 9.0]
        })
        
        stats = ffs.compute_stats_for_scoring(field_data, lab_data)
        self.assertIn('pH', stats)
        self.assertIn('EC_uS_cm', stats)
        self.assertIn('DO_mg_L', stats)
        self.assertIn('q10', stats['pH'])
        self.assertIn('q50', stats['pH'])
        self.assertIn('q90', stats['pH'])

    def test_quality_penalties(self):
        """Test quality penalty computation"""
        stats = {
            'pH': {'q10': 6.5, 'q50': 7.0, 'q90': 7.5},
            'EC_uS_cm': {'q10': 100, 'q50': 200, 'q90': 300},
            'DO_mg_L': {'q10': 7.0, 'q50': 8.0, 'q90': 9.0},
            'redox_mV': {'q10': 150, 'q50': 200, 'q90': 250}
        }
        
        ideal_pred = {'pH': 7.0, 'EC_uS_cm': 100, 'DO_mg_L': 9.0, 'redox_mV': 200}
        penalties = ffs.quality_penalties(ideal_pred, stats)
        self.assertAlmostEqual(penalties['pH'], 0.0, places=2)
        self.assertAlmostEqual(penalties['EC_uS_cm'], 0.0, places=2)
        self.assertAlmostEqual(penalties['DO_mg_L'], 0.0, places=2)
        self.assertAlmostEqual(penalties['redox_mV'], 0.0, places=2)
        
        worst_pred = {'pH': 8.5, 'EC_uS_cm': 400, 'DO_mg_L': 5.0, 'redox_mV': 300}
        penalties = ffs.quality_penalties(worst_pred, stats)
        self.assertAlmostEqual(penalties['pH'], 1.0, places=2)
        self.assertAlmostEqual(penalties['EC_uS_cm'], 1.0, places=2)
        self.assertAlmostEqual(penalties['DO_mg_L'], 1.0, places=2)
        self.assertLessEqual(penalties['redox_mV'], 1.0)

    def test_score_from_penalties(self):
        """Test computing score from penalties"""
        penalties = {'pH': 0.2, 'EC_uS_cm': 0.3, 'DO_mg_L': 0.1, 'redox_mV': 0.4}
        weights = {'pH': 0.35, 'EC_uS_cm': 0.35, 'DO_mg_L': 0.20, 'redox_mV': 0.10}
        
        score = ffs.score_from_penalties(penalties, weights)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)
        
        empty_score = ffs.score_from_penalties({}, weights, default_if_empty=65.0)
        self.assertEqual(empty_score, 65.0)

    def test_category_from_score(self):
        """Test categorizing score"""
        cfg = {'good': 70.0, 'fair': 40.0, 'poor': 0.0}
        
        self.assertEqual(ffs.category_from_score(80.0, cfg), 'good')
        self.assertEqual(ffs.category_from_score(70.0, cfg), 'good')
        self.assertEqual(ffs.category_from_score(69.9, cfg), 'fair')
        self.assertEqual(ffs.category_from_score(40.0, cfg), 'fair')
        self.assertEqual(ffs.category_from_score(39.9, cfg), 'poor')
        self.assertEqual(ffs.category_from_score(0.0, cfg), 'poor')

    def test_normalize_site_id(self):
        """Test normalizing site IDs"""
        self.assertEqual(ffs._normalize_site_id(123), "123")
        self.assertEqual(ffs._normalize_site_id(123.0), "123")
        self.assertEqual(ffs._normalize_site_id("123"), "123")
        self.assertEqual(ffs._normalize_site_id(" site_1 "), "site_1")
        self.assertIsNone(ffs._normalize_site_id(None))
        self.assertIsNone(ffs._normalize_site_id(float('nan')))

    @patch('pandas.read_csv')
    def test_list_sites(self, mock_read_csv):
        """Test listing sites"""
        test_data = pd.DataFrame({
            'Site ID': ['1', '2', '3', '4'],
            'Victorian Suburb': ['Melbourne', 'Melbourne', 'Perth', 'Sydney']
        })
        mock_read_csv.return_value = test_data
        
        sites = ffs.list_sites()
        
        self.assertEqual(len(sites), 4)
        self.assertEqual(sites[0]['id'], '1')
        self.assertEqual(sites[0]['suburb'], 'Melbourne - 1 Area')
        self.assertEqual(sites[1]['suburb'], 'Melbourne - 2 Area')
        self.assertEqual(sites[2]['suburb'], 'Perth')  
        self.assertEqual(sites[3]['suburb'], 'Sydney')  

    def test_predict_site_no_data(self):
        """Test predicting a site with no data (fallback)"""
        result = ffs.predict_site("test_site", horizon_days=7)
        
        self.assertIn('predictions', result)
        self.assertIn('quality', result)
        self.assertIn('usage', result)
        self.assertIn('median', result['usage'].values())
        self.assertGreaterEqual(result['quality']['score'], 0)
        self.assertLessEqual(result['quality']['score'], 100)

    @patch('main.services.future_family_safety_service.get_model')
    @patch('main.services.future_family_safety_service.get_global_model')
    def test_predict_site_with_models(self, mock_get_global_model, mock_get_model):
        """Test predicting a site with models"""
        mock_model_obj = {
            'model': MagicMock(),
            'param_name': 'pH',
            'last_vals': [7.0, 7.1, 7.2],
            'last_dt': pd.Timestamp('2023-01-01'),
            'baseline_window': 5,
            'rmse_model': 0.1,
            'rmse_baseline': 0.2
        }
        mock_get_model.return_value = mock_model_obj
        
        mock_global_obj = {
            'model': MagicMock(),
            'meta': {
                'param_name': 'EC_uS_cm',
                'hot_sites': ['test_site'],
                'feature_names': ['lag1', 'lag2']
            }
        }
        mock_get_global_model.return_value = mock_global_obj
        
        mock_model_obj['model'].predict.return_value = [7.3]
        mock_global_obj['model'].predict.return_value = [250.0]
        
        result = ffs.predict_site("test_site", horizon_days=7)
        
        self.assertIn('predictions', result)
        self.assertIn('quality', result)
        self.assertIn('usage', result)
        
        usage_keys = list(result['usage'].keys())
        self.assertIn('pH', usage_keys)
        self.assertIn('EC_uS_cm', usage_keys)

    def test_health_payload(self):
        """Test health check payload"""
        payload = ffs.health_payload()
        self.assertTrue(payload['ok'])
        self.assertIn('ts', payload)
        self.assertIn('artifacts_dir', payload)

    def test_clear_caches(self):
        """Test clearing caches"""
        ffs.get_clean_data()
        ffs.get_stats()
        ffs.clear_caches()
        
        with ffs._cache_lock:
            self.assertIsNone(ffs._field_df_cache)
            self.assertIsNone(ffs._lab_wide_df_cache)
            self.assertIsNone(ffs._stats_cache)
            self.assertEqual(ffs._models_cache, {})
            self.assertIsNone(ffs._sites_cache)

    def test_forecast_48h_series(self):
        """Test 48-hour forecast series generation"""
        series = ffs._forecast_48h_series(70.0, 0.8)
        
        self.assertEqual(len(series), 9)
        for point in series:
            self.assertIn('ts', point)
            self.assertIn('do_nothing', point)
            self.assertIn('take_action', point)
            self.assertIn('ci_low', point)
            self.assertIn('ci_high', point)
            self.assertGreaterEqual(point['do_nothing'], 0)
            self.assertLessEqual(point['do_nothing'], 100)
            self.assertGreaterEqual(point['take_action'], 0)
            self.assertLessEqual(point['take_action'], 100)

    def test_horizon_clamping(self):
        """Test prediction horizon clamping"""
        result = ffs.predict_site("test_site", horizon_days=1000)
        result = ffs.predict_site("test_site", horizon_days=-5)


if __name__ == '__main__':
    import unittest
    unittest.main()
