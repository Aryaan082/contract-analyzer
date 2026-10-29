// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Pool {
    string constant PAD = "xxxxxxxxxx";
    uint256 public fee;

    function setFee(uint256 f) external {
        fee = f;
    }
}
