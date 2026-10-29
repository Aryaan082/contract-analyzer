// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice A pool with a settable fee, kept in basis points.
/// The fee is read by the swap code.
contract Pool {
    uint256 public fee;

    function setFee(uint256 f) external {
        fee = f; // no caller check
    }
}
