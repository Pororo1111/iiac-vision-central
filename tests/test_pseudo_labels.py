"""자동 선별이 미확인 객체를 정답으로 편입하지 않는지 검사한다."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from pseudo_labels import align_hard_hat, fill_missing, select


def box(label='person', confidence=0.9, xywh=None):
    return {'label':label,'confidence':confidence,'xywh':xywh or [0.5,0.5,0.4,0.4]}


class SelectionTest(unittest.TestCase):
    def test_consensus_accepts_normalized_boxes(self):
        result=select(['person'],[box()])
        self.assertTrue(result['include'])
        self.assertEqual(len(result['labels']),1)
    def test_disagreement_excludes_entire_image(self):
        self.assertFalse(select(['person','hard_hat'],[box()])['include'])
        self.assertFalse(select(['person'],[box(),box('traffic_cone')])['include'])
    def test_low_confidence_object_is_not_silently_removed(self):
        self.assertFalse(select(['person'],[box(),box(confidence=0.4)])['include'])
    def test_empty_dark_and_invalid_boxes_are_excluded(self):
        for tags,detections in [([],[]),(['person'],[]),(['person'],[box(xywh=[0,0,1,1])]),(['person'],[box(confidence=float('nan'))])]:
            self.assertFalse(select(tags,detections)['include'])



class FillMissingTest(unittest.TestCase):
    def test_fills_only_classes_missing_from_source(self):
        result=fill_missing([box(),box('hard_hat',0.3)],{3})
        self.assertTrue(result['include'])
        self.assertEqual([line.split()[0] for line in result['labels']],['0'])
    def test_ambiguous_missing_class_excludes_image(self):
        self.assertFalse(fill_missing([box(),box(confidence=0.4)],{3})['include'])
    def test_no_detection_keeps_image(self):
        self.assertEqual(fill_missing([],{3}),{'include':True,'reason':'빠진 클래스 채움','labels':[]})
    def test_hard_hat_uses_model_box_inside_source_box(self):
        head=(3,[0.5,0.5,0.2,0.3])
        self.assertEqual(align_hard_hat([head,(0,[0.5,0.5,0.4,0.8])],[box('hard_hat',0.4,[0.5,0.42,0.18,0.15])]),
                         [(3,[0.5,0.42,0.18,0.15]),(0,[0.5,0.5,0.4,0.8])])
    def test_hard_hat_without_model_box_excludes_image(self):
        self.assertIsNone(align_hard_hat([(3,[0.5,0.5,0.2,0.3])],[box('hard_hat',0.9,[0.1,0.1,0.1,0.1])]))

if __name__=='__main__': unittest.main()
