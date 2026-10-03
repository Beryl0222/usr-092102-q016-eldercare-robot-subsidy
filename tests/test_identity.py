import unittest

from src.identity import IdentityLink, IdentityRegistry, MutexResolution, LinkStatus, is_mutex_pair


class MutexRuleTest(unittest.TestCase):
    def test_only_same_mutex_group_is_exclusive(self) -> None:
        self.assertTrue(is_mutex_pair("care_lease", "care_lease"))
        self.assertFalse(is_mutex_pair("exo_purchase", "care_lease"))  # 购置+租赁可并用
        self.assertFalse(is_mutex_pair(None, "care_lease"))
        self.assertFalse(is_mutex_pair(None, None))


class IdentityTest(unittest.TestCase):
    def _registry_with_link(self) -> tuple[IdentityRegistry, str]:
        reg = IdentityRegistry()
        reg.suggest(IdentityLink("L1", "E-A", "E-B", ("姓名一致", "证件号码不一致")))
        return reg, "L1"

    def test_suggested_link_does_not_block_funds(self) -> None:
        reg, lid = self._registry_with_link()
        # 待核对关联绝不阻断资金，也不能只按姓名去重
        self.assertFalse(reg.is_blocked("E-A"))
        self.assertFalse(reg.is_blocked("E-B"))
        self.assertEqual(len(reg.pending_links_for("E-A")), 1)

    def test_confirmed_allow_coexist_does_not_block(self) -> None:
        reg, lid = self._registry_with_link()
        reg.confirm(lid, decided_by="staff-1", resolution=MutexResolution.ALLOW_COEXIST)
        self.assertFalse(reg.is_blocked("E-A"))

    def test_confirmed_block_blocks_only_after_confirmation(self) -> None:
        reg, lid = self._registry_with_link()
        reg.confirm(lid, decided_by="staff-1", resolution=MutexResolution.BLOCK)
        self.assertTrue(reg.is_blocked("E-A"))
        self.assertTrue(reg.is_blocked("E-B"))

    def test_dismissed_link_means_legal_coexist(self) -> None:
        reg, lid = self._registry_with_link()
        reg.dismiss(lid, decided_by="staff-1", note="非同一人")
        self.assertFalse(reg.is_blocked("E-B"))
        self.assertEqual(reg._links[lid].status, LinkStatus.DISMISSED)

    def test_link_cannot_be_decided_twice(self) -> None:
        reg, lid = self._registry_with_link()
        reg.dismiss(lid, decided_by="staff-1")
        with self.assertRaises(ValueError):
            reg.confirm(lid, decided_by="staff-2", resolution=MutexResolution.BLOCK)


if __name__ == "__main__":
    unittest.main()
