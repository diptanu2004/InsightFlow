from insightflow_backend.auth.passwords import hash_password, verify_password


def test_hash_is_not_the_plaintext():
    hashed = hash_password("hunter2")
    assert hashed != "hunter2"


def test_verify_accepts_correct_password():
    hashed = hash_password("hunter2")
    assert verify_password("hunter2", hashed) is True


def test_verify_rejects_wrong_password():
    hashed = hash_password("hunter2")
    assert verify_password("wrong", hashed) is False


def test_same_password_hashes_differently_each_time():
    # bcrypt salts per-hash -- two hashes of the same password must never match byte-for-byte,
    # otherwise a leaked password table would let an attacker spot repeated passwords.
    assert hash_password("hunter2") != hash_password("hunter2")
