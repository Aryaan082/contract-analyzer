// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IPairLike {
    function getReserves() external view returns (uint112, uint112, uint32);
}

interface ITwapLike {
    function consult() external view returns (uint256);
}

contract CollateralLender {
    IPairLike public immutable pair;
    ITwapLike public immutable twap;
    mapping(address => uint256) public collateral;
    mapping(address => uint256) public debt;

    constructor(IPairLike pair_, ITwapLike twap_) {
        pair = pair_;
        twap = twap_;
    }

    function lock(uint256 amount) external {
        collateral[msg.sender] += amount;
    }

    function borrow(uint256 amount) external {
        uint256 value = collateral[msg.sender] * twap.consult() / 1e18;
        require((debt[msg.sender] + amount) * 3 <= value * 2, "undercollateralised");
        debt[msg.sender] += amount;
    }
}
