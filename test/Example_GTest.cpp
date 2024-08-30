#include <gtest/gtest.h>

class FakeFixture : public testing::Test
{
  virtual void SetUp() {}
  virtual void TearDown() {}
}

TEST_F(FakeFixture, DISABLED_test) {
}

TEST_F(FakeFixture, test_numerical) {
  double x = 0.0;
  EXPECT_EQ(10.0 / x, 0);
}

TEST_F(FakeFixture, test_failure) {
  EXPECT_TRUE(false);
}
